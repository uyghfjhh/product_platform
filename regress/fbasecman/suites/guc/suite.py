"""GUC regression suite entry point."""

import time
from pathlib import Path

from framework.configuration import load_regression_config
from .manifest import GUC_CASES, case_items, find_case
from .runtime import GucRuntime
from .executors import (
    execute_search_path_reuse_sql_parse,
    execute_search_path_reuse_hint,
    execute_search_path_multivalue_sql_parse,
    execute_search_path_multivalue_hint,
    execute_search_path_empty_normalize,
    execute_search_path_mixed_quotes_cleanup,
    execute_reset_param_sql_parse,
    execute_reset_param_hint,
    execute_reset_all_sql_parse,
    execute_reset_all_hint,
    execute_discard_all_sql_parse,
    execute_discard_all_hint,
    execute_set_local_transaction_sql_parse,
    execute_set_local_transaction_hint,
    execute_case_insensitive_quotes_sql_parse,
    execute_case_insensitive_quotes_hint,
    execute_report_param_timezone_sql_parse,
    execute_report_param_timezone_hint,
)


EXECUTORS = {
    "search_path_reuse_sql_parse": execute_search_path_reuse_sql_parse,
    "search_path_reuse_hint": execute_search_path_reuse_hint,
    "search_path_multivalue_sql_parse": execute_search_path_multivalue_sql_parse,
    "search_path_multivalue_hint": execute_search_path_multivalue_hint,
    "search_path_empty_normalize": execute_search_path_empty_normalize,
    "search_path_mixed_quotes_cleanup": execute_search_path_mixed_quotes_cleanup,
    "reset_param_sql_parse": execute_reset_param_sql_parse,
    "reset_param_hint": execute_reset_param_hint,
    "reset_all_sql_parse": execute_reset_all_sql_parse,
    "reset_all_hint": execute_reset_all_hint,
    "discard_all_sql_parse": execute_discard_all_sql_parse,
    "discard_all_hint": execute_discard_all_hint,
    "set_local_transaction_sql_parse": execute_set_local_transaction_sql_parse,
    "set_local_transaction_hint": execute_set_local_transaction_hint,
    "case_insensitive_quotes_sql_parse": execute_case_insensitive_quotes_sql_parse,
    "case_insensitive_quotes_hint": execute_case_insensitive_quotes_hint,
    "report_param_timezone_sql_parse": execute_report_param_timezone_sql_parse,
    "report_param_timezone_hint": execute_report_param_timezone_hint,
}


def show():
    lines = ["guc - GUC 规范化、多值解析与连接复用同步测试"]
    for case in GUC_CASES:
        lines.append("  - %-42s %s" % (case.target, case.summary))
    return "\n".join(lines)


def run_case(root, case):
    started = time.monotonic()
    runtime = None
    try:
        runtime = GucRuntime(root, case)
        with runtime:
            EXECUTORS[case.executor](runtime)
        runtime.finish("PASS", "GUC 规范化、多值解析及连接复用重放验证通过；所有检测项符合预期。")
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
            run_root = env.output_dir / "runs" / "guc" / case.name
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
