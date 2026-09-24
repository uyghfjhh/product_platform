"""Framework-native read/write routing regression suite."""

import re
import time
from pathlib import Path

from framework.configuration import load_regression_config
from suites.ha_commands.runtime import HaCommandFailure
from .manifest import RW_TOGGLE_CASES, case_items, find_case, validate_manifest
from .runtime import RwToggleRuntime


def show():
    return "\n".join(
        ["rw_toggle - framework-native read/write routing"] +
        ["  - %-40s topology=%-12s mode=%-5s driver=%s %s" % (
            case.target, case.topology, case.route_mode, case.driver, case.summary)
         for case in RW_TOGGLE_CASES]
    )


def _group(case):
    return "mmr_group" if case.topology == "mmr" else "rep_group"


def _route_title(case):
    return "%s %s/%s" % (case.topology.upper(), case.route_mode.upper(), case.driver.upper())


def _group_rows(output, group):
    """Parse the psql table returned by SHOW GROUP_ROUTING."""
    lines = [line.strip() for line in output.splitlines() if "|" in line]
    header_index = next(
        (index for index, line in enumerate(lines)
         if line.startswith("group_name") and "group_mode" in line), None)
    if header_index is None:
        return []
    headers = [item.strip() for item in lines[header_index].split("|")]
    rows = []
    for line in lines[header_index + 1:]:
        if set(line.replace("|", "").replace("-", "").strip()) == set():
            continue
        values = [item.strip() for item in line.split("|")]
        if len(values) != len(headers) or values[0] != group:
            continue
        rows.append(dict(zip(headers, values)))
    return rows


def _check_group_fields(rt, case, output):
    group = _group(case)
    expected_mode = "mmr" if case.topology == "mmr" else "replication"
    expected_role = "write-leader" if case.topology == "mmr" else "primary"
    rows = _group_rows(output, group)
    fields = (
        "group_name", "group_mode", "cluster_name", "candidate_node",
        "effective_grouprole", "effective_state", "is_write_target",
        "route_status",
    )
    actual_rows = ["; ".join("%s=%s" % (field, row.get(field, "<missing>"))
                   for field in fields) for row in rows]
    all_common_fields = bool(rows) and all(
        row.get("group_mode") == expected_mode and
        row.get("effective_state") == "active" and
        row.get("route_status") == "AVAILABLE"
        for row in rows)
    role_rows = [row for row in rows
                 if row.get("effective_grouprole") == expected_role and
                 row.get("is_write_target") == "true"]
    passed = all_common_fields and bool(role_rows)
    expected = (
        "每行 group_name=%s、group_mode=%s、effective_state=active、"
        "route_status=AVAILABLE；至少一行 effective_grouprole=%s 且 "
        "is_write_target=true" % (group, expected_mode, expected_role)
    )
    rt.check(
        "%s：逐字段校验 SHOW GROUP_ROUTING" % _route_title(case),
        expected,
        "返回行数=%d\n%s" % (len(rows), "\n".join(actual_rows) or "<no parsed rows>"),
        passed,
    )


def _start_and_show(rt, case):
    expected_mode = "mmr" if case.topology == "mmr" else "replication"
    expected_role = "write-leader" if case.topology == "mmr" else "primary"
    rt.start()
    output = rt.psql(
        "SHOW GROUP_ROUTING %s;" % _group(case),
        "%s：检查路由配置已加载" % _route_title(case),
        "%s group_mode=%s，且存在 active %s 候选" %
        (_group(case), expected_mode, expected_role),
        lambda output: (_group(case) in output and expected_mode in output and
                        "active" in output and expected_role in output),
    )
    _check_group_fields(rt, case, output)


def _read_sql():
    return ("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY; "
            "SELECT inet_server_addr(), inet_server_port(), pg_is_in_recovery();")


def _write_sql():
    return ("SET SESSION CHARACTERISTICS AS TRANSACTION READ WRITE; BEGIN; "
            "CREATE TEMP TABLE rw_toggle_probe(id integer); "
            "INSERT INTO rw_toggle_probe VALUES (1); "
            "SELECT inet_server_addr(), inet_server_port(), pg_is_in_recovery(); ROLLBACK;")


def _run_psql_case(rt, case):
    _start_and_show(rt, case)
    group = _group(case)
    if case.route_mode == "port":
        read_port = rt.read_port
        write_port = rt.listen_port
    else:
        read_port = rt.listen_port
        write_port = rt.listen_port

    def run_read():
        output = rt.psql_business(
            _read_sql(), "%s：执行只读事务" % _route_title(case),
            "只读事务返回后端地址、端口和 recovery 状态",
            lambda text: bool(re.search(r"\|\s*\d{4,5}\s*\|", text)),
            group=group, port=read_port,
        )
        rt.assert_backend(output, "read", "%s：只读请求命中合法读候选" % _route_title(case))

    def run_write():
        output = rt.psql_business(
            _write_sql(), "%s：执行写事务" % _route_title(case),
            "写事务创建临时表并返回后端地址、端口",
            lambda text: bool(re.search(r"\|\s*\d{4,5}\s*\|", text)),
            group=group, port=write_port,
        )
        rt.assert_backend(output, "write", "%s：写请求命中 write-leader" % _route_title(case))

    if case.scenario == "read":
        run_read()
    elif case.scenario == "write":
        run_write()
    elif case.scenario == "switch":
        run_write()
        run_read()
        run_write()
    else:
        raise HaCommandFailure("unsupported psql scenario: %s" % case.scenario)


def _run_case_body(rt, case):
    if case.driver == "jdbc":
        _start_and_show(rt, case)
        rt.run_jdbc(case.route_mode)
    else:
        _run_psql_case(rt, case)


def run_case(root, case):
    started = time.monotonic()
    runtime = None
    try:
        runtime = RwToggleRuntime(root, case)
        with runtime:
            _run_case_body(runtime, case)
            runtime.check_product_log()
        runtime.finish("PASS", "新框架执行的路由、后端目标和连接清理检测项全部通过。")
        print("%-58s SUCCESS %8.3fs" % (case.target, time.monotonic() - started))
        return True
    except Exception as exc:
        if runtime is not None:
            try:
                runtime.finish("FAIL", str(exc))
            except Exception:
                runtime.stop()
        else:
            env = load_regression_config(Path(root))
            run_root = env.output_dir / "runs" / "rw_toggle" / case.name
            run_root.mkdir(parents=True, exist_ok=True)
            (run_root / "report.txt").write_text(
                "用例: %s\n结论: FAIL\n失败原因: %s\n" % (case.target, exc),
                encoding="utf-8")
        print("%-58s FAIL    %8.3fs: %s" % (case.target, time.monotonic() - started, exc))
        return False


def run(root, target=None):
    validate_manifest()
    selected = [find_case(target)] if target else case_items()
    failures = sum(0 if run_case(root, case) else 1 for case in selected)
    print("Total: SUCCESS:%d FAIL:%d" % (len(selected) - failures, failures))
    return failures == 0
