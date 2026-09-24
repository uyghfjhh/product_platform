import tempfile
import unittest
from pathlib import Path

from framework.evidence.log_checks import find_forbidden_log_patterns


class FrameworkChecksTest(unittest.TestCase):
    def test_reports_the_path_for_each_forbidden_pattern(self):
        with tempfile.TemporaryDirectory() as tmp:
            clean = Path(tmp) / "clean.log"
            bad = Path(tmp) / "bad.log"
            clean.write_text("normal\n", encoding="utf-8")
            bad.write_text("assertion failed\nsegmentation fault\n", encoding="utf-8")

            found = find_forbidden_log_patterns(
                [clean, bad], ["assertion failed", "segmentation fault"]
            )

        self.assertEqual(
            found,
            [
                {"pattern": "assertion failed", "path": str(bad)},
                {"pattern": "segmentation fault", "path": str(bad)},
            ],
        )
