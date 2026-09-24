import tempfile
import unittest
from pathlib import Path

from framework.reporting.html import generate_html_report, export_html_from_runs


class TestHtmlReport(unittest.TestCase):
    def test_generate_html_report_contains_kpi_and_cases(self):
        results = [
            {
                "suite": "ha_commands",
                "case": "case_1",
                "target": "ha_commands.case_1",
                "status": "PASS",
                "duration": 1.5,
                "message": "",
                "details": "Step 1: OK\nStep 2: OK",
            },
            {
                "suite": "ha_commands",
                "case": "case_2",
                "target": "ha_commands.case_2",
                "status": "FAIL",
                "duration": 2.0,
                "message": "Connection timeout",
                "details": "Step 1: FAIL (timeout)",
            },
        ]
        with tempfile.TemporaryDirectory() as tmp_dir:
            out_file = Path(tmp_dir) / "report.html"
            content = generate_html_report(results, output_file=out_file, title="Custom Title")
            self.assertTrue(out_file.exists())
            self.assertIn("Custom Title", content)
            self.assertIn("ha_commands.case_1", content)
            self.assertIn("ha_commands.case_2", content)
            self.assertIn("Connection timeout", content)
            self.assertIn("50.0%", content)  # Pass rate (1 / 2)


if __name__ == "__main__":
    unittest.main()
