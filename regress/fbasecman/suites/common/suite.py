"""Common regression suite entry point."""

import time
from pathlib import Path

from framework.configuration import load_regression_config
from .manifest import COMMON_CASES, case_items, find_case, validate_manifest
from .helpers import CommonRuntime
from .executors import (
    _run_console_commands,
    _run_err_logger_rotation,
    _run_route_stats_quantiles,
    _run_worker_thread_lifecycle,
)

EXECUTORS = {
    "console_commands": _run_console_commands,
    "err_logger_rotation": _run_err_logger_rotation,
    "route_stats_quantiles": _run_route_stats_quantiles,
    "worker_thread_lifecycle": _run_worker_thread_lifecycle,
}


def show():
    validate_manifest()
    lines = ["common - 常规功能与控制台管理测试"]
    for case in COMMON_CASES:
        lines.append("  - %-42s 来源=%s %s" % (
            case.target, ",".join(case.source_sections), case.summary,
        ))
    lines.append("")
    lines.append("Default gate: %d / %d" % (len(case_items()), len(COMMON_CASES)))
    return "\n".join(lines)


def run_case(root, case):
    started = time.monotonic()
    rt = None
    try:
        rt = CommonRuntime(root, case)
        with rt:
            summary_reason = EXECUTORS[case.executor](rt)
        if not summary_reason:
            summary_reason = "%s 验证通过。" % case.summary
        rt.finish("PASS", summary_reason)
        print("%-58s SUCCESS %8.3fs" % (case.target, time.monotonic() - started))
        return True
    except Exception as exc:
        if rt is not None:
            try:
                rt.finish("FAIL", str(exc))
            except Exception:
                rt.stop()
        else:
            run_root = (load_regression_config(Path(root)).output_dir / "runs" /
                        "common" / case.name)
            run_root.mkdir(parents=True, exist_ok=True)
            (run_root / "report.txt").write_text(
                "用例: %s\n结论: FAIL\n验证目的: %s\n失败原因: %s\n" %
                (case.target, case.summary, exc), encoding="utf-8")
        print("%-58s FAIL    %8.3fs" % (case.target, time.monotonic() - started))
        return False


def run(root, target=None):
    validate_manifest()
    selected = [find_case(target)] if target else case_items()
    failures = sum(0 if run_case(root, case) else 1 for case in selected)
    print("Total: SUCCESS:%d FAIL:%d" % (len(selected) - failures, failures))
    return failures == 0
