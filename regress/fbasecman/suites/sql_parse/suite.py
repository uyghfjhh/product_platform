"""SQL_PARSE extended-protocol regression suite entry point."""

import time
from pathlib import Path

from framework.configuration import load_regression_config
from suites.ha_commands.runtime import HaCommandFailure, HaCommandRuntime
from suites.ha_commands.suite import (
    _run_sql_parse_heartbeat_bind_invalid,
    _run_sql_parse_heartbeat_bind_normal,
    _run_sql_parse_heartbeat_bind_unsupported,
)
from .manifest import SQL_PARSE_CASES, case_items, find_case


EXECUTORS = {
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
    started = time.monotonic()
    runtime = None
    try:
        runtime = HaCommandRuntime(root, case)
        with runtime:
            EXECUTORS[case.executor](runtime)
        runtime.finish("PASS", "SQL_PARSE 配置、协议响应及异常处理证据均符合预期。")
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
            run_root = env.output_dir / "runs" / "sql_parse" / case.name
            run_root.mkdir(parents=True, exist_ok=True)
            (run_root / "report.txt").write_text(
                "Test: %s\nStatus: FAIL\nSummary: %s\nFailure: %s\n" %
                (case.target, case.summary, exc), encoding="utf-8")
        print("%-58s FAIL    %8.3fs" % (case.target, time.monotonic() - started))
        return False


def run(root, target=None):
    selected = [find_case(target)] if target else case_items()
    failures = sum(0 if run_case(root, case) else 1 for case in selected)
    print("Total: SUCCESS:%d FAIL:%d" % (len(selected) - failures, failures))
    return failures == 0
