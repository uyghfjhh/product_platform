import sys
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from framework.reporting.junit import (
    generate_junit_xml,
    collect_results_from_runs,
    export_junit_from_runs,
)


class JUnitTest(unittest.TestCase):
    def test_generate_junit_xml_structure(self):
        results = [
            {
                "suite": "high_availability",
                "case": "core_13_monitor_confirm",
                "status": "PASS",
                "duration": 1.25,
                "message": "",
                "details": "All steps passed",
                "system_out": "stdout log text",
            },
            {
                "suite": "high_availability",
                "case": "core_14_rep_standby_failure",
                "status": "FAIL",
                "duration": 0.85,
                "message": "Step 3 failed",
                "details": "AssertionError: expected active replica but got None",
                "system_out": "stderr output",
            },
            {
                "suite": "ha_commands",
                "case": "show_clusters_test",
                "status": "UNTESTED",
                "duration": 0.0,
                "message": "",
                "details": "",
            },
        ]

        xml_content = generate_junit_xml(results, suite_name="regression_test")
        self.assertTrue(xml_content.startswith("<?xml"))

        # Parse and assert ElementTree
        root = ET.fromstring(xml_content)
        self.assertEqual("testsuites", root.tag)
        self.assertEqual("3", root.attrib["tests"])
        self.assertEqual("1", root.attrib["failures"])
        self.assertEqual("1", root.attrib["skipped"])

        suites = root.findall("testsuite")
        self.assertEqual(2, len(suites))

        ha_suite = next(s for s in suites if s.attrib["name"] == "high_availability")
        self.assertEqual("2", ha_suite.attrib["tests"])
        self.assertEqual("1", ha_suite.attrib["failures"])

        pass_case = next(tc for tc in ha_suite.findall("testcase") if tc.attrib["name"] == "core_13_monitor_confirm")
        self.assertEqual(0, len(pass_case.findall("failure")))
        self.assertEqual("1.250", pass_case.attrib["time"])

        fail_case = next(tc for tc in ha_suite.findall("testcase") if tc.attrib["name"] == "core_14_rep_standby_failure")
        failures = fail_case.findall("failure")
        self.assertEqual(1, len(failures))
        self.assertEqual("Step 3 failed", failures[0].attrib["message"])

    def test_export_junit_from_runs(self):
        runs_dir = ROOT_DIR / "output" / "runs"
        with TemporaryDirectory() as tmpdir:
            out_file = Path(tmpdir) / "junit_report.xml"
            xml_str = export_junit_from_runs(runs_dir, output_file=out_file)

            self.assertTrue(out_file.exists())
            self.assertIn("<testsuites", xml_str)

            root = ET.parse(str(out_file)).getroot()
            self.assertEqual("testsuites", root.tag)
            self.assertGreater(int(root.attrib["tests"]), 0)


if __name__ == "__main__":
    unittest.main()
