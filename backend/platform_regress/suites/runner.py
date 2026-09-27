"""Shared execution lifecycle for declarative regression cases."""

import time
from pathlib import Path
from platform_regress.suites.contracts import SuiteRunResult


def run_runtime_case(root, case, runtime_type, executors, pass_reason,
                     initialization_step=None):
    """Run one case and preserve a report if runtime initialization fails."""
    started = time.monotonic()
    runtime = None
    try:
        runtime = runtime_type(root, case)
        with runtime:
            executors[case.executor](runtime)
        runtime.finish("PASS", pass_reason)
        print("%-58s SUCCESS %8.3fs" % (case.target, time.monotonic() - started))
        return True
    except Exception as exc:
        if runtime is not None:
            try:
                runtime.finish("FAIL", str(exc))
            except Exception:
                runtime.stop()
        else:
            run_root = Path(root) / "output" / "runs" / case.suite_id / case.name
            run_root.mkdir(parents=True, exist_ok=True)
            report = (
                "Test: %s\nStatus: FAIL\nSummary: %s\nFailure: %s\n"
                % (case.target, case.summary, exc)
            )
            if initialization_step:
                report += "Steps: %s\n" % initialization_step
            (run_root / "report.txt").write_text(report, encoding="utf-8")
        print("%-58s FAIL    %8.3fs" % (case.target, time.monotonic() - started))
        return False


def run_cases(root, selected, run_case):
    """Run selected cases and emit the common suite summary."""
    return bool(run_cases_result(root, selected, run_case))


def run_cases_result(root, selected, run_case):
    """Run cases and return a normalized result without changing reports."""
    failures = 0
    total = len(selected)
    results = []
    for index, case in enumerate(selected, 1):
        passed = run_case(root, case)
        results.append((case.target, "PASS" if passed else "FAIL"))
        if not passed:
            failures += 1
    print(
        "Total: SUCCESS:%d FAIL:%d" % (total - failures, failures),
        flush=True,
    )
    suite_id = selected[0].suite_id if selected else ""
    return SuiteRunResult(suite_id, total, total - failures, failures, results)
