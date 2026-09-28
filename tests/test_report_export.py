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
    def test_suite_result_json_is_authoritative(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "suite-result.json").write_text(json.dumps({
                "targets": ["s.a", "s.b"],
                "results": [case_result("s.a", "PASS"),
                            case_result("s.b", "BLOCKED", "缺依赖")],
            }), encoding="utf-8")
            rows = collect_results_from_run(root)
            self.assertEqual(["s.a", "s.b"], [r["target"] for r in rows])
            self.assertEqual("SKIPPED", rows[1]["status"])
            self.assertEqual("缺依赖", rows[1]["message"])

    def test_single_result_json(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "result.json").write_text(
                json.dumps(case_result("s.only", "FAIL", "nope")),
                encoding="utf-8")
            rows = collect_results_from_run(root)
            self.assertEqual([("s.only", "FAIL")],
                             [(r["target"], r["status"]) for r in rows])

    def test_per_target_child_dirs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, verdict in (("mmr.a", "PASS"), ("mmr.b", "FAIL")):
                child = root / name
                child.mkdir()
                (child / "result.json").write_text(
                    json.dumps(case_result(name, verdict)), encoding="utf-8")
            (root / "_sessions").mkdir()  # session scratch dirs are skipped
            rows = collect_results_from_run(root)
            self.assertEqual(["PASS", "FAIL"], [r["status"] for r in rows])

    def test_legacy_runs_layout_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            case_dir = root / "runs" / "suite1" / "case_a"
            case_dir.mkdir(parents=True)
            (case_dir / "summary.json").write_text(
                json.dumps({"status": "PASS", "duration": 2.0}),
                encoding="utf-8")
            rows = collect_results_from_run(root)
            self.assertEqual([("suite1", "case_a", "PASS")],
                             [(r["suite"], r["case"], r["status"]) for r in rows])


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
