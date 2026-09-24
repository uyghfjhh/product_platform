"""High Availability regression suite entry point and executors (Chapter 4)."""

import time
from pathlib import Path

from framework.configuration import load_regression_config
from .manifest import HIGH_AVAILABILITY_CASES, case_items, find_case
from .runtime import HighAvailabilityRuntime

from .executors.phase1_console import (
    run_core_19_set_node_atomicity,
    run_core_20_write_promoted_refresh_show,
    run_core_21_reload_parameters_and_structure,
    run_core_22_reload_failure_protection,
)
from .executors.phase2_failure import (
    run_core_13_monitor_confirm,
    run_core_14_rep_standby_failure,
    run_core_15_rep_primary_failure,
    run_core_18_balance_single_failure,
)
from .executors.phase3_failover import (
    run_core_16_rep_failover,
    run_core_17_mmr_write_center_failover,
)

EXECUTORS = {
    "core_13_monitor_confirm": run_core_13_monitor_confirm,
    "core_14_rep_standby_failure": run_core_14_rep_standby_failure,
    "core_15_rep_primary_failure": run_core_15_rep_primary_failure,
    "core_16_rep_failover": run_core_16_rep_failover,
    "core_17_mmr_write_center_failover": run_core_17_mmr_write_center_failover,
    "core_18_balance_single_failure": run_core_18_balance_single_failure,
    "core_19_set_node_atomicity": run_core_19_set_node_atomicity,
    "core_20_write_promoted_refresh_show": run_core_20_write_promoted_refresh_show,
    "core_21_reload_parameters_and_structure": run_core_21_reload_parameters_and_structure,
    "core_22_reload_failure_protection": run_core_22_reload_failure_protection,
}


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
    failures = sum(0 if run_case(root, case) else 1 for case in selected)
    print("Total: SUCCESS:%d FAIL:%d" % (len(selected) - failures, failures))
    return failures == 0
