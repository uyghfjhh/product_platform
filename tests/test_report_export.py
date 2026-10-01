"""Platform tests for fact-model report export (JUnit + HTML)."""

import json
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from platform_regress.reporting.export import (
    collect_results_from_run, export_junit, export_html, row_from_case_result,
    rows_from_results,
)


def case_result(target, verdict, reason=None, duration=1.5, evidence=()):
    return {
        "schema_version": "1.0", "execution_id": "e1", "operation_id": None,
        "target": target, "verdict": verdict, "business_verdict": verdict,
        "reason": reason,
        "cleanup": {"status": "PASS", "reason": None},
        "duration_seconds": duration, "evidence": list(evidence),
    }


class RowMappingTest(unittest.TestCase):
    def test_verdicts_map_to_report_status(self):
        rows = rows_from_results([
            case_result("s.a", "PASS"),
            case_result("s.b", "FAIL", "boom"),
            case_result("s.c", "ERROR", "crash"),
            case_result("s.d", "BLOCKED", "缺少命令: foo"),
            case_result("s.e", "CANCELLED"),
        ])
        self.assertEqual(
            ["PASS", "FAIL", "ERROR", "SKIPPED", "SKIPPED"],
            [row["status"] for row in rows])

    def test_target_splits_into_suite_and_case(self):
        row = row_from_case_result(case_result("mmr.core_01", "PASS"))
        self.assertEqual("mmr", row["suite"])
        self.assertEqual("core_01", row["case"])

    def test_evidence_listed_in_details(self):
        row = row_from_case_result(
            case_result("s.a", "FAIL", "assertion", evidence=["artifacts/e1/x.log"]))
        self.assertIn("artifacts/e1/x.log", row["details"])
        self.assertIn("assertion", row["details"])


class CollectFromRunTest(unittest.TestCase):
    def test_cases_are_collected_from_canonical_directories(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for target, verdict in (("s.a", "PASS"), ("s.b", "FAIL")):
                case = root / "cases" / target
                case.mkdir(parents=True)
                (case / "result.json").write_text(json.dumps(case_result(target, verdict)))
            (root / "suite-result.json").write_text(json.dumps({"results": [case_result("s.a", "FAIL")]}))
            rows = collect_results_from_run(root)
            self.assertEqual(["PASS", "FAIL"], [row["status"] for row in rows])

    def test_missing_facts_do_not_guess_verdict_from_text(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            case = root / "cases" / "s.a"
            case.mkdir(parents=True)
            (case / "report.txt").write_text("Status: PASS")
            self.assertEqual([], collect_results_from_run(root))


class ExportTest(unittest.TestCase):
    def test_junit_xml_from_case_results(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "junit.xml"
            xml_text = export_junit(
                [case_result("s.a", "PASS"), case_result("s.b", "FAIL", "x")],
                output_file=output, suite_name="demo")
            root = ET.fromstring(xml_text)
            self.assertEqual("demo", root.attrib["name"])
            self.assertEqual("2", root.attrib["tests"])
            self.assertEqual("1", root.attrib["failures"])
            self.assertTrue(output.exists())
            cases = root.findall(".//testcase")
            self.assertEqual("a", cases[0].attrib["name"])
            self.assertIsNotNone(cases[1].find("failure"))

    def test_blocked_becomes_skipped_in_junit(self):
        xml_text = export_junit([case_result("s.a", "BLOCKED", "缺依赖")])
        root = ET.fromstring(xml_text)
        self.assertEqual("1", root.attrib["skipped"])
        skipped = root.find(".//skipped")
        self.assertIsNotNone(skipped)

    def test_html_from_case_results(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "report.html"
            content = export_html(
                [case_result("s.a", "PASS")],
                output_file=output, title="Demo 报告")
            self.assertIn("Demo 报告", content)
            self.assertIn('"status": "PASS"', content)
            self.assertTrue(output.exists())


if __name__ == "__main__":
    unittest.main()
