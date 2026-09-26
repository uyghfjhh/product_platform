"""SQL_PARSE extended-protocol regression suite entry point."""

from framework.suites.runner import run_cases, run_runtime_case
from products.fbasecman.case_runtime import FbasecmanCaseRuntime
from suites.ha_commands.suite import (
    _run_sql_parse_heartbeat_bind_invalid,
    _run_sql_parse_heartbeat_bind_normal,
    _run_sql_parse_heartbeat_bind_unsupported,
)
from .manifest import SQL_PARSE_CASES, case_items, find_case
from .executors import run_savepoint_recovery_after_local_25p02


EXECUTORS = {
    "savepoint_recovery_after_local_25p02": run_savepoint_recovery_after_local_25p02,
    "heartbeat_bind_normal": _run_sql_parse_heartbeat_bind_normal,
    "heartbeat_bind_invalid": _run_sql_parse_heartbeat_bind_invalid,
    "heartbeat_bind_unsupported": _run_sql_parse_heartbeat_bind_unsupported,
}


def show():
    lines = ["sql_parse - SQL_PARSE 扩展协议测试"]
    for case in SQL_PARSE_CASES:
        lines.append("  - %-42s %s" % (case.target, case.summary))
    return "\n".join(lines)


def run_case(root, case):
    return run_runtime_case(
        root, case, FbasecmanCaseRuntime, EXECUTORS,
        "SQL_PARSE 配置、协议响应及异常处理证据均符合预期。",
    )


def run(root, target=None):
    selected = [find_case(target)] if target else case_items()
    return run_cases(root, selected, run_case)
