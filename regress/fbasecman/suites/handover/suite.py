"""Executors and entry point for the formal handover transfer-test inventory."""

import time
from pathlib import Path

from framework.execution.locking import ExclusiveFileLock
from suites.handover.manifest import HANDOVER_CASES, case_items, find_case, validate_manifest
from suites.handover.runtime import HandoverFailure, HandoverRuntime
from suites.handover.executors import dispatch_executor
from suites.handover.helpers import *

# Business titles for global prepared statement phase observation
_GLOBAL_PREPARED_TITLES = {
    "DEFAULT": "默认读写状态查询后（未执行读写切换）",
}


def _dispatch_case(rt):
    """Dispatch execution to the dedicated case executor."""
    executor_fn = dispatch_executor(rt.case)
    return executor_fn(rt)


# Maintain EXECUTORS dictionary mapping case.executor -> function for backward compatibility
# and unit-test patching support.
EXECUTORS = {
    "configuration": _dispatch_case,
    "lifecycle": _dispatch_case,
    "hint_set": _dispatch_case,
    "hint_begin": _dispatch_case,
    "node_status": _dispatch_case,
    "port_write": _dispatch_case,
    "port_read": _dispatch_case,
    "ha": _dispatch_case,
    "jdbc": _dispatch_case,
    "console_metadata": _dispatch_case,
    "console_maintenance": _dispatch_case,
    "console_stats": _dispatch_case,
    "console_thread_pool_stats": _dispatch_case,
    "console_reset_stats": _dispatch_case,
    "long_statistics": _dispatch_case,
    "heartbeat": _dispatch_case,
    "guc_sync": _dispatch_case,
    "attach": _dispatch_case,
    "parse_error": _dispatch_case,
    "global_ps": _dispatch_case,
    "global_ps_special": _dispatch_case,
    "global_ps_eviction": _dispatch_case,
    "global_ps_bypass": _dispatch_case,
}


def show():
    validate_manifest()
    lines = ["handover - fbasecman 转测文档第 4 至 11 章业务用例"]
    for case in HANDOVER_CASES:
        flag = "[LONG-TIME] " if case.long_time else ""
        lines.append("  - %-52s %s来源=%s %s" % (case.target, flag, ",".join(case.source_sections), case.summary))
    lines.append("")
    lines.append("Default gate: %d / %d (LONG-TIME excluded)" % (len(case_items(False)), len(case_items(True))))
    return "\n".join(lines)


def _result_line(target, status, elapsed):
    """Keep case status output aligned while preserving the real elapsed time."""
    return "%-68s %-16s %8.3fs" % (target, status, elapsed)


def run_case(root, case):
    started = time.monotonic()
    rt = HandoverRuntime(root, case)
    try:
        executor = EXECUTORS.get(case.executor, _dispatch_case)
        executor(rt)
    except Exception as exc:
        try:
            rt.finish("FAIL", str(exc))
        except Exception:
            # Report rendering must never prevent process cleanup.
            rt.stop()
        suffix = " [%s]" % case.issue_id if getattr(case, "issue_id", None) else ""
        print(_result_line(case.target, "FAIL%s" % suffix, time.monotonic() - started))
        return False
    except BaseException:
        # KeyboardInterrupt and SystemExit still own a case-local process.
        rt.stop()
        raise
    finally:
        rt.stop()
    rt.finish("PASS", "文档规定的 SQL/JDBC 结果、路由、console 状态和业务统计均满足预期。")
    print(_result_line(case.target, "SUCCESS", time.monotonic() - started))
    return True


def run(root, target=None):
    validate_manifest()
    selected = [find_case(target)] if target else case_items(include_long_time=False)
    unknown = [case.executor for case in selected if case.executor not in EXECUTORS and case.name not in dispatch_executor.__globals__["CASE_EXECUTORS"]]
    if unknown:
        raise HandoverFailure("missing executors: %s" % ", ".join(sorted(set(unknown))))
    lock_path = Path(root) / "output" / "handover.lock"
    with ExclusiveFileLock(lock_path, "handover suite"):
        failures = 0
        for case in selected:
            failures += 0 if run_case(root, case) else 1
        print("Total: SUCCESS:%d FAIL:%d" % (len(selected) - failures, failures))
        return failures == 0
