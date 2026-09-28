"""High Availability regression suite entry point and executors (Chapter 4)."""

import time
from pathlib import Path

from cmanconf import load_regression_config
from platform_regress.suites.runner import run_cases
from .manifest import HIGH_AVAILABILITY_CASES, case_items, find_case
from .runtime import HighAvailabilityRuntime

from .dispatch import EXECUTORS


def show():
    lines = ["high_availability - 高可用故障切换与 monitor 探测（方案第四章）"]
    for case in HIGH_AVAILABILITY_CASES:
        lines.append("  - %-50s [%s] %s" % (case.target, case.core_id, case.summary))
    return "\n".join(lines)


def run_case(root, case):
    started = time.monotonic()
    runtime = None
    try:
        runtime = HighAvailabilityRuntime(root, case)
        with runtime:
            executor_fn = EXECUTORS.get(case.name)
            if executor_fn:
                executor_fn(runtime)
            else:
                raise NotImplementedError("Executor for %s not implemented" % case.name)

        runtime.finish(
            "PASS",
            "%s；报告所列操作均执行成功，全部检测项符合预期。" % case.summary,
        )
        elapsed = time.monotonic() - started
        print("%-58s SUCCESS %8.3fs" % (case.target, elapsed))
        return True
    except Exception as exc:
        if runtime is not None:
            try:
                runtime.add_failure_diagnostic(exc)
                runtime.finish("FAIL", "用例执行失败: %s" % exc)
            except Exception:
                pass
        elapsed = time.monotonic() - started
        print("%-58s FAIL    %8.3fs: %s" % (case.target, elapsed, exc))
        return False


def run(root, target=None):
    selected = [find_case(target)] if target else case_items()
    return run_cases(root, selected, run_case)
