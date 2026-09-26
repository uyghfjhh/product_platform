"""SQL_PARSE savepoint recovery protocol regression."""

import json
import re
import sys

from suites.ha_commands.helpers import _sql_parse_transform
from suites.ha_commands.runtime import HaCommandFailure


EXPECTED = {
    "begin": (None, "T", "BEGIN", None),
    "savepoint": (None, "T", "SAVEPOINT", None),
    "division": ("22012", "E", None, None),
    "aborted_select": ("25P02", "E", None, None),
    "rollback_to": (None, "T", "ROLLBACK", None),
    "recovery_select": (None, "T", "SELECT 1", "9"),
    "cleanup": (None, "I", "ROLLBACK", None),
}

RESPONSE_NAMES = {
    "1": "ParseComplete", "2": "BindComplete", "n": "NoData",
    "T": "RowDescription", "D": "DataRow", "C": "CommandComplete",
    "E": "ErrorResponse", "Z": "ReadyForQuery",
}


def _expected_text(step):
    sqlstate, ready, tag, value = EXPECTED[step]
    return "client P/B/D/E/S; SQLSTATE=%s, ReadyForQuery=%s, CommandComplete=%s, value=%s" % (
        sqlstate or "none", ready, tag or "none", value or "none")


def _matches(record):
    expected = EXPECTED[record["step"]]
    sqlstate, ready, tag, value = expected
    received = record["received"]
    return (record["sent"] == "P/B/D/E/S" and
            record["sqlstate"] == sqlstate and record["ready"] == ready and
            record["command_tag"] == tag and record["value"] == value and
            ("E" in received if sqlstate else "C" in received) and
            received[-1] == "Z")


def run_savepoint_recovery_after_local_25p02(rt):
    conf = rt.start(transform=_sql_parse_transform("mmr_group"))
    config = conf.read_text(encoding="utf-8")
    rt.check(
        "确认 SQL_PARSE 与 prepared statement 保留配置",
        "rw_split_method=sql_parse, pool_reserve_prepared_statement=yes",
        "配置文件 %s\n%s" % (conf, "\n".join(
            line.strip() for line in config.splitlines()
            if "rw_split_method" in line or "pool_reserve_prepared_statement" in line)),
        'rw_split_method "sql_parse"' in config and
        'pool_reserve_prepared_statement yes' in config,
    )
    probe = rt.root / "suites" / "sql_parse" / "assets" / "savepoint_recovery_probe.py"
    rc, output = rt.run_command(
        [sys.executable, str(probe), str(rt.listen_port)],
        rt.logs_dir / "savepoint_recovery_protocol.log", cwd=rt.workdir,
        check=False, record=False,
    )
    records = []
    for line in output.splitlines():
        if line.startswith("STEP_JSON="):
            records.append(json.loads(line[len("STEP_JSON="):]))
    expected_order = (
        [("direct_recovery", step) for step in
         ("begin", "savepoint", "division", "rollback_to", "recovery_select", "cleanup")] +
        [("after_local_25p02", step) for step in
         ("begin", "savepoint", "division", "aborted_select", "rollback_to",
          "recovery_select", "cleanup")]
    )
    if rc != 0 or [(row["variant"], row["step"]) for row in records] != expected_order:
        rt.record_step(
            "扩展协议复现程序执行与步骤完整性",
            "python3 %s %s" % (probe, rt.listen_port),
            "13 条 SQL 均收到 ReadyForQuery；程序正常退出",
            "rc=%s, steps=%s\n%s" % (rc, len(records), output), "FAIL")
        raise HaCommandFailure("savepoint recovery probe did not complete all SQL steps")

    failed = []
    for row in records:
        passed = _matches(row)
        title = "%s / %s" % (row["variant"], row["step"])
        actual = "received=%s, SQLSTATE=%s, ReadyForQuery=%s, CommandComplete=%s, value=%s" % (
            "/".join("%s(%s)" % (kind, RESPONSE_NAMES.get(kind, "unknown"))
                     for kind in row["received"]), row["sqlstate"], row["ready"],
            row["command_tag"], row["value"])
        if row.get("error"):
            actual += ", error=%s" % row["error"]
        rt.record_step(
            title,
            "client Extended P(Parse)/B(Bind)/D(Describe)/E(Execute)/S(Sync): %s" %
            row["sql"],
            _expected_text(row["step"]), actual,
            "PASS" if passed else "FAIL")
        if not passed:
            failed.append(title)

    log_lines = rt.proxy_log.read_text(encoding="utf-8", errors="replace").splitlines()
    error_row = next(row for row in records if row["variant"] == "after_local_25p02"
                     and row["step"] == "aborted_select")
    client_id_match = re.search(r"fbasecman: ([0-9a-f]+):", error_row.get("error") or "")
    client_id = client_id_match.group(1) if client_id_match else ""
    relevant = [line for line in log_lines if
                (client_id and client_id in line) and
                any(word in line.lower() for word in
                    ("rollback", "25p02", "savepoint", "local error", "detach"))]
    internal_rollback = any("after internal rollback" in line for line in relevant)
    rt.record_step(
        "代理事务恢复日志及内部 Q(ROLLBACK) 证据",
        "fbasecman.log: %s" % rt.proxy_log,
        "同一 client 的保存点恢复期间不应发生内部完整 ROLLBACK；日志与客户端回包相互核对",
        "client_id=%s\n%s" % (client_id or "<未从错误消息提取>",
                              "\n".join(relevant[-30:]) or
                              "<本轮无匹配日志；以协议回包判定>"),
        "FAIL" if internal_rollback else "PASS")
    if internal_rollback:
        failed.append("proxy internal ROLLBACK destroyed savepoint")
    if failed:
        raise HaCommandFailure("savepoint recovery mismatch: %s" % ", ".join(failed))
