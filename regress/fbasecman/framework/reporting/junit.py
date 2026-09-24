"""JUnit XML report generator for fbasecman_regress_v2 test suites."""

import json
import os
import re
import xml.etree.ElementTree as ET
from xml.dom import minidom
from pathlib import Path
from typing import Any, Dict, List, Optional


def generate_junit_xml(
    results: List[Dict[str, Any]],
    output_file: Optional[Path] = None,
    suite_name: str = "fbasecman_regression",
) -> str:
    """Generate standard JUnit XML string from a list of test case result dicts.

    Each result dict format:
    {
        "suite": "high_availability",
        "case": "core_13_monitor_confirm",
        "status": "PASS" | "FAIL" | "ERROR" | "SKIPPED" | "UNTESTED",
        "duration": 1.23,  # seconds
        "message": "Optional short failure summary",
        "details": "Optional full backtrace, logs, or report snippet",
        "system_out": "Optional stdout text",
    }
    """
    # Group results by suite
    suites_map: Dict[str, List[Dict[str, Any]]] = {}
    for r in results:
        s_name = r.get("suite", "default")
        suites_map.setdefault(s_name, []).append(r)

    total_tests = len(results)
    total_failures = sum(1 for r in results if r.get("status") == "FAIL")
    total_errors = sum(1 for r in results if r.get("status") == "ERROR")
    total_skipped = sum(1 for r in results if r.get("status") in ("SKIPPED", "UNTESTED"))
    total_time = sum(float(r.get("duration") or 0.0) for r in results)

    root = ET.Element(
        "testsuites",
        {
            "name": suite_name,
            "tests": str(total_tests),
            "failures": str(total_failures),
            "errors": str(total_errors),
            "skipped": str(total_skipped),
            "time": f"{total_time:.3f}",
        },
    )

    for s_name, case_list in sorted(suites_map.items()):
        suite_tests = len(case_list)
        suite_failures = sum(1 for r in case_list if r.get("status") == "FAIL")
        suite_errors = sum(1 for r in case_list if r.get("status") == "ERROR")
        suite_skipped = sum(1 for r in case_list if r.get("status") in ("SKIPPED", "UNTESTED"))
        suite_time = sum(float(r.get("duration") or 0.0) for r in case_list)

        ts_el = ET.SubElement(
            root,
            "testsuite",
            {
                "name": s_name,
                "tests": str(suite_tests),
                "failures": str(suite_failures),
                "errors": str(suite_errors),
                "skipped": str(suite_skipped),
                "time": f"{suite_time:.3f}",
            },
        )

        for c in case_list:
            tc_time = float(c.get("duration") or 0.0)
            tc_el = ET.SubElement(
                ts_el,
                "testcase",
                {
                    "classname": s_name,
                    "name": c.get("case", "unknown"),
                    "time": f"{tc_time:.3f}",
                },
            )

            status = (c.get("status") or "UNTESTED").upper()
            if status == "FAIL":
                fail_el = ET.SubElement(
                    tc_el,
                    "failure",
                    {
                        "message": c.get("message") or "Test failed",
                        "type": "AssertionError",
                    },
                )
                fail_el.text = c.get("details") or c.get("message") or ""
            elif status == "ERROR":
                err_el = ET.SubElement(
                    tc_el,
                    "error",
                    {
                        "message": c.get("message") or "Test encountered runtime error",
                        "type": "RuntimeError",
                    },
                )
                err_el.text = c.get("details") or c.get("message") or ""
            elif status in ("SKIPPED", "UNTESTED"):
                skip_el = ET.SubElement(
                    tc_el,
                    "skipped",
                    {
                        "message": c.get("message") or "Case was not executed or skipped",
                    },
                )

            if c.get("system_out"):
                so_el = ET.SubElement(tc_el, "system-out")
                so_el.text = c["system_out"]

    raw_xml = ET.tostring(root, encoding="utf-8")
    parsed = minidom.parseString(raw_xml)
    pretty_xml = parsed.toprettyxml(indent="  ", encoding="utf-8").decode("utf-8")

    if output_file:
        out_p = Path(output_file)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        out_p.write_text(pretty_xml, encoding="utf-8")

    return pretty_xml


def collect_results_from_runs(runs_dir: Path) -> List[Dict[str, Any]]:
    """Scan output/runs/ directory and extract structured test execution results."""
    results: List[Dict[str, Any]] = []
    if not runs_dir.exists():
        return results

    for suite_dir in sorted(runs_dir.iterdir()):
        if not suite_dir.is_dir() or suite_dir.name.startswith("."):
            continue
        suite_name = suite_dir.name
        for case_dir in sorted(suite_dir.iterdir()):
            if not case_dir.is_dir():
                continue
            case_name = case_dir.name
            target = f"{suite_name}.{case_name}"

            status = "UNTESTED"
            duration = 0.0
            message = ""
            details = ""
            sys_out = ""

            summary_file = case_dir / "summary.json"
            if summary_file.exists():
                try:
                    s_data = json.loads(summary_file.read_text(encoding="utf-8"))
                    status = s_data.get("status", "PASS").upper()
                    duration = float(s_data.get("duration", 0.0) or 0.0)
                except Exception:
                    pass

            report_file = case_dir / "report.txt"
            if report_file.exists():
                try:
                    rep_text = report_file.read_text(encoding="utf-8", errors="replace")
                    sys_out = rep_text
                    if status == "UNTESTED":
                        st_m = re.search(r"^(?:结论|Status):\s*(PASS|FAIL)", rep_text, re.M)
                        if st_m:
                            status = st_m.group(1).upper()
                    if duration == 0.0:
                        t1_m = re.search(r"^测试开始时间:\s*(.*)$", rep_text, re.M)
                        t2_m = re.search(r"^测试结束时间:\s*(.*)$", rep_text, re.M)
                        if t1_m and t2_m:
                            try:
                                from datetime import datetime
                                d1 = datetime.strptime(t1_m.group(1).strip(), "%Y-%m-%d %H:%M:%S")
                                d2 = datetime.strptime(t2_m.group(1).strip(), "%Y-%m-%d %H:%M:%S")
                                duration = abs((d2 - d1).total_seconds())
                            except Exception:
                                pass
                    # Look for failure reason
                    fail_m = re.search(r"^(?:失败原因|Failure Reason):\s*(.*)$", rep_text, re.M)
                    if fail_m:
                        message = fail_m.group(1).strip()
                    details = rep_text
                except Exception:
                    pass

            # Check for crash backtrace
            bt_file = case_dir / "backtrace.txt"
            if bt_file.exists():
                try:
                    bt_text = bt_file.read_text(encoding="utf-8", errors="replace")
                    details += f"\n\n=== GDB Backtrace ===\n{bt_text}"
                    if not message:
                        message = "C proxy crashed (GDB backtrace captured)"
                except Exception:
                    pass

            results.append({
                "suite": suite_name,
                "case": case_name,
                "target": target,
                "status": status,
                "duration": duration,
                "message": message,
                "details": details,
                "system_out": sys_out,
            })

    return results


def export_junit_from_runs(runs_dir: Path, output_file: Optional[Path] = None) -> str:
    """Convenience helper to read all runs and export a JUnit XML."""
    results = collect_results_from_runs(runs_dir)
    return generate_junit_xml(results, output_file=output_file)
