"""Report export from the platform fact model.

The JUnit and HTML renderers consume plain row dicts.  These helpers map
``CaseResult`` (or its ``to_dict`` payload) onto those rows so one run can
export both formats from the same fact model — verdicts come from
``result.json``/``suite-result.json``, never from rendered report text.

Verdict mapping onto the renderer status vocabulary:
``PASS``/``FAIL``/``ERROR`` map directly; ``BLOCKED`` and ``CANCELLED``
become ``SKIPPED`` (dependency gate or interruption, not a business
failure); anything else is ``UNTESTED``.
"""

import json
from pathlib import Path

from platform_regress.reporting.html import generate_html_report
from platform_regress.reporting.junit import (
    generate_junit_xml,
)

_STATUS_MAP = {
    "PASS": "PASS",
    "FAIL": "FAIL",
    "ERROR": "ERROR",
    "BLOCKED": "SKIPPED",
    "CANCELLED": "SKIPPED",
}


def row_from_case_result(result):
    """Map one CaseResult (or to_dict payload) onto a report row dict."""
    if hasattr(result, "to_dict"):
        result = result.to_dict()
    target = result.get("target") or ""
    suite, separator, case = target.partition(".")
    verdict = (result.get("verdict") or "").upper()
    status = _STATUS_MAP.get(verdict, "UNTESTED")
    reason = result.get("reason") or ""
    evidence = list(result.get("evidence") or [])
    details = reason
    if evidence:
        details += ("\n\n" if details else "") + "证据附件:\n" + "\n".join(evidence)
    return {
        "suite": suite if separator else "default",
        "case": case or target,
        "target": target,
        "status": status,
        "duration": float(result.get("duration_seconds") or 0.0),
        "message": reason,
        "details": details,
        "system_out": "",
        "steps": list(result.get("steps") or []),
    }


def collect_results_from_run(output_dir):
    """Collect authoritative CaseResult files from a canonical run directory."""
    rows = []
    for path in sorted((Path(output_dir) / "cases").glob("*/result.json")):
        rows.append(row_from_case_result(json.loads(path.read_text())))
    return rows


def rows_from_results(results):
    """Convert CaseResult objects (or dicts) to report rows."""
    return [row_from_case_result(result) for result in results]


def export_junit(results, output_file=None, suite_name="regression"):
    """Export a JUnit XML report from CaseResult objects."""
    return generate_junit_xml(
        rows_from_results(results),
        output_file=Path(output_file) if output_file else None,
        suite_name=suite_name)


def export_html(results, output_file=None, title="回归测试执行报告"):
    """Export a self-contained HTML report from CaseResult objects."""
    return generate_html_report(
        rows_from_results(results),
        output_file=Path(output_file) if output_file else None,
        title=title)


def export_run_reports(results, *, junit_path=None, html_path=None,
                       title="回归测试执行报告", suite_name="regression"):
    """Write both report formats for one completed run."""
    if junit_path:
        export_junit(results, output_file=junit_path, suite_name=suite_name)
    if html_path:
        export_html(results, output_file=html_path, title=title)
