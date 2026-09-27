"""Last-failed-case bookkeeping shared by regression CLI front-ends.

A run records the targets whose latest verdict is not PASS so a later
``run failed`` invocation reruns exactly those. Products supply the
registry (for target validation) and the resolved output directory.
"""

import json
import re
from pathlib import Path

from platform_regress.persistence import atomic_write_text


def case_status(output_dir, target):
    """Return PASS/FAIL verdict for a suite.case target, or None if unknown."""
    suite_name, separator, case_name = target.partition(".")
    if not separator:
        return None
    run_root = Path(output_dir) / "runs" / suite_name / case_name
    summary = run_root / "summary.json"
    if summary.exists():
        try:
            return json.loads(summary.read_text(encoding="utf-8")).get("status")
        except (OSError, ValueError):
            return None
    report = run_root / "report.txt"
    if report.exists():
        match = re.search(
            r"^(?:结论|Status):\s*(PASS|FAIL)\s*$",
            report.read_text(encoding="utf-8", errors="replace"),
            re.MULTILINE,
        )
        return match.group(1) if match else None
    return None


def last_failed_path(output_dir):
    return Path(output_dir) / "last_failed.json"


def read_last_failed(output_dir, registry=None):
    """Read recorded failed targets; drop entries no longer selectable."""
    try:
        value = json.loads(last_failed_path(output_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    targets = value.get("targets", []) if isinstance(value, dict) else []
    if registry is not None:
        targets = [t for t in targets if registry.selected_targets(t) == [t]]
    return targets


def write_last_failed(output_dir, targets):
    atomic_write_text(
        last_failed_path(output_dir),
        json.dumps({"targets": list(targets)}, ensure_ascii=False, indent=2),
    )


def failed_targets(targets, result, output_dir):
    """Compute which targets to record after a run finishes with ``result``.

    A preflight or infrastructure failure produces no fresh report; previous
    PASS verdicts must not erase the target from `run failed` bookkeeping.
    """
    failures = [t for t in targets if case_status(output_dir, t) != "PASS"]
    return failures or (list(targets) if result != 0 else [])


def rerun_failed(output_dir, registry, run_target):
    """Rerun recorded failures; ``run_target(target) -> int`` per target."""
    targets = read_last_failed(output_dir, registry)
    if not targets:
        write_last_failed(output_dir, [])
        return targets, [], True
    failures = []
    for target in targets:
        try:
            result = run_target(target)
        except Exception:
            result = 1
        if result != 0 or case_status(output_dir, target) != "PASS":
            failures.append(target)
    write_last_failed(output_dir, failures)
    return targets, failures, not failures
