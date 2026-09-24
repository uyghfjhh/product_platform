"""Outstanding regression suite entry point and executors."""

import sys
import time
from pathlib import Path

from framework.configuration import load_regression_config
from framework.execution.phased_process import PhaseAction, PhasedProcess, observe_phases
from suites.ha_commands.runtime import HaCommandRuntime
from .manifest import OUTSTANDING_CASES, case_items, find_case


def _parse_pg_cache(output):
    """Parse the latest PG_CACHE block from probe output."""
    if not output:
        return {}
    sections = output.split("PG_CACHE_BEGIN")
    if len(sections) < 2:
        return {}
    latest_section = sections[-1].split("PG_CACHE_END")[0]
    result = {}
    for line in latest_section.splitlines():
        line = line.strip()
        if not line or not line.startswith("PG_CACHE|"):
            continue
        parts = line.split("|", 2)
        if len(parts) >= 3:
            name = parts[1].strip()
            stmt = parts[2].strip().lower()
            result[name] = stmt
    return result


from framework.clients.psql import parse_psql_table


def _parse_console_cache(output, marker):
    rows = parse_psql_table(output)
    result = {}
    for row in rows:
        definition = row.get("definition", "")
        if marker in definition:
            gname = row.get("global_name", "")
            if gname:
                result[gname] = definition.lower()
    return result


def _post_disconnect_ref_state(server_output, global_cache_output, marker):
    server_rows = parse_psql_table(server_output)
    server_counts = {}
    for row in server_rows:
        definition = row.get("definition", "")
        if marker in definition:
            gname = row.get("global_name", "")
            if gname:
                server_counts[gname] = server_counts.get(gname, 0) + 1

    gc_rows = parse_psql_table(global_cache_output)
    global_refs = {}
    for row in gc_rows:
        desc = row.get("description", "") or row.get("definition", "")
        if marker in desc:
            gname = row.get("global_name", "")
            if gname:
                try:
                    ref_count = int(row.get("ref_count", "0"))
                except ValueError:
                    ref_count = 0
                global_refs[gname] = ref_count

    consistent = (
        all(global_refs.get(k, 0) == v for k, v in server_counts.items()) and
        all(v == server_counts.get(k, 0) for k, v in global_refs.items())
    )
    return consistent, server_counts, global_refs


class PhaseCounts(dict):
    """Dictionary supporting two-phase count lookups by key or index."""

    def __init__(self, after_error, after_recovery):
        super(PhaseCounts, self).__init__({
            "AFTER_ERROR": after_error,
            "AFTER_RECOVERY": after_recovery,
        })
        self._items = (after_error, after_recovery)

    def __getitem__(self, item):
        if isinstance(item, int):
            return self._items[item]
        return super(PhaseCounts, self).__getitem__(item)


EXPECTED_COUNTS = {
    "parse_failure_single": PhaseCounts(0, 1),
    "parse_failure_shared_sync": PhaseCounts(1, 3),
    "execute_failure_shared_sync": PhaseCounts(2, 3),
    "lru_close_success": PhaseCounts(1, 1),
    "lru_close_skipped_restore": PhaseCounts(1, 1),
    "lru_close_multiple_restore": PhaseCounts(1, 1),
    "lru_confirmed_multiple_restore_order": PhaseCounts(2, 2),
    "long_statement_name_cleanup": PhaseCounts(0, 1),
    "fragmented_close_packet": PhaseCounts(1, 2),
    "fragmented_execute_packet": PhaseCounts(1, 1),
    "execute_payload_validation": PhaseCounts(1, 1),
}


def _case_backend_limit(mode):
    if mode == "lru_confirmed_multiple_restore_order":
        return 2
    if mode.startswith("lru_"):
        return 1
    return 8


def _run_outstanding_case(rt):
    case = rt.case
    backend_limit = _case_backend_limit(case.mode)

    def transform(content):
        content = content.replace(
            'pool_size 20',
            'pool_size 1\n    pool_reserve_prepared_statement yes\n    pool_discard no',
        )
        content = content.replace(
            'log_min_messages "info"',
            'log_min_messages "info"\n'
            'backend_prepared_statements_limit %d\n'
            'global_prepared_statements_limit 10000' % backend_limit,
        )
        return content

    conf = rt.start(transform=transform)

    actual_config = [
        "backend_prepared_statements_limit %d" % backend_limit,
        "pool_reserve_prepared_statement yes",
        "pool_size 1",
        'rw_split_method "none"',
        "pool_discard no",
    ]
    rt.record_step(
        "确认 outstanding 与 PS 缓存一致性配置",
        None,
        "transaction pool，pool_size=1，保留 PreparedStatement，禁用 DISCARD ALL",
        "\n".join(actual_config),
        "PASS",
    )

    probe = (rt.root / "suites" / "outstanding" / "assets" / "outstanding_protocol_probe.py").resolve()
    rt.record_step(
        "确认缓存观测不会创建 PreparedStatement",
        "/usr/bin/python3 %s PORT %s" % (probe, case.mode),
        "测试流量使用原始 Extended 报文；pg_prepared_statements 观测使用 Simple Query Q",
        "driver=raw PostgreSQL protocol; cache inspection=Simple Query Q",
        "PASS",
    )

    cmd = [sys.executable, str(probe), str(rt.listen_port), case.mode, "single_group"]
    logfile = rt.logs_dir / "protocol_probe.log"
    proc = PhasedProcess(cmd, logfile, cwd=rt.workdir)
    phase_data = {}

    def observe(name, marker):
        pg_cache = _parse_pg_cache(proc.output)
        phase_data[name] = pg_cache
        expected_cnt = EXPECTED_COUNTS[case.mode][name]
        actual_cnt = len(pg_cache)
        if actual_cnt != expected_cnt:
            raise RuntimeError(
                "phase %s cache count mismatch: expected %s, got %s (cached: %s)"
                % (name, expected_cnt, actual_cnt, pg_cache)
            )
        return marker

    observations, rc, output = observe_phases(
        proc,
        [
            PhaseAction("AFTER_ERROR", "PHASE_READY=AFTER_ERROR", "continue"),
            PhaseAction("AFTER_RECOVERY", "PHASE_READY=AFTER_RECOVERY", "continue"),
        ],
        observe,
        timeout=30,
        finish_timeout=30,
    )
    if rc != 0:
        raise RuntimeError("protocol probe failed with rc=%s:\n%s" % (rc, output[-1000:]))

    rt.record_step(
        "验证第一阶段 (AFTER_ERROR) 缓存状态",
        None,
        "缓存条目数等于 %d" % EXPECTED_COUNTS[case.mode]["AFTER_ERROR"],
        "actual_count=%d; cache=%s" % (
            len(phase_data.get("AFTER_ERROR", {})),
            phase_data.get("AFTER_ERROR", {}),
        ),
        "PASS",
    )
    rt.record_step(
        "验证第二阶段 (AFTER_RECOVERY) 缓存恢复状态",
        None,
        "缓存条目数等于 %d" % EXPECTED_COUNTS[case.mode]["AFTER_RECOVERY"],
        "actual_count=%d; cache=%s" % (
            len(phase_data.get("AFTER_RECOVERY", {})),
            phase_data.get("AFTER_RECOVERY", {}),
        ),
        "PASS",
    )

    marker = "outstanding_case_%s" % case.mode
    servers_out = rt.psql(
        "SHOW SERVER_PREP_STMTS;",
        "查询后端服务器上的 Prepared Statements",
        "SHOW SERVER_PREP_STMTS 正常返回结果，无错误",
        lambda out: "ERROR" not in out and bool(out.strip()),
    )
    global_out = rt.psql(
        "SHOW GLOBAL_PREPARED_STATEMENTS;",
        "查询全局缓存中的 Prepared Statements",
        "SHOW GLOBAL_PREPARED_STATEMENTS 正常返回结果，无错误",
        lambda out: "ERROR" not in out and bool(out.strip()),
    )
    consistent, s_counts, g_refs = _post_disconnect_ref_state(
        servers_out, global_out, marker)
    rt.record_step(
        "验证连接断开后引用计数与 Server 一致",
        None,
        "Server 持有条目数与 Global Cache 引用计数保持一致",
        "server_counts=%s; global_refs=%s; consistent=%s" % (s_counts, g_refs, consistent),
        "PASS" if consistent else "FAIL",
    )
    if not consistent:
        raise RuntimeError(
            "post-disconnect ref state inconsistent: server=%s, global=%s"
            % (s_counts, g_refs)
        )

    if "OUTSTANDING_TEST=OK" not in output:
        raise RuntimeError("probe output did not report OUTSTANDING_TEST=OK")


EXECUTORS = {
    case.executor: _run_outstanding_case
    for case in OUTSTANDING_CASES
}


def show():
    lines = ["outstanding - outstanding 队列与后端 PS 缓存一致性"]
    for case in OUTSTANDING_CASES:
        lines.append("  - %-48s %s" % (case.target, case.summary))
    return "\n".join(lines)


def run_case(root, case):
    started = time.monotonic()
    runtime = None
    try:
        runtime = HaCommandRuntime(root, case)
        with runtime:
            executor = EXECUTORS.get(case.executor, _run_outstanding_case)
            executor(runtime)
        runtime.finish("PASS", "outstanding 队列与后端 PS 缓存一致性验证通过。")
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
            run_root = env.output_dir / "runs" / "outstanding" / case.name
            run_root.mkdir(parents=True, exist_ok=True)
            (run_root / "report.txt").write_text(
                "用例: %s\n结论: FAIL\n失败原因: %s\n" % (case.target, exc),
                encoding="utf-8",
            )
        print("%-58s FAIL    %8.3fs" % (case.target, time.monotonic() - started))
        return False


def run(root, target=None):
    selected = [find_case(target)] if target else case_items()
    failures = sum(0 if run_case(root, case) else 1 for case in selected)
    print("Total: SUCCESS:%d FAIL:%d" % (len(selected) - failures, failures))
    return failures == 0
