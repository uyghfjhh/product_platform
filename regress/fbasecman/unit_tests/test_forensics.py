import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from framework.execution.forensics import (
    find_core_files,
    signal_name,
    diagnose_crash,
    extract_gdb_backtrace,
)


class ForensicsUnitTest(unittest.TestCase):
    def test_signal_name(self):
        self.assertEqual("SIGSEGV", signal_name(-11))
        self.assertEqual("SIGSEGV", signal_name(139))
        self.assertEqual("SIGABRT", signal_name(134))
        self.assertEqual("SIGBUS", signal_name(135))
        self.assertEqual("RC_0", signal_name(0))

    def test_find_core_files(self):
        with TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            core1 = tmppath / "core.fbasecman.1234.5678"
            core1.write_text("fake core data")
            core2 = tmppath / "core"
            core2.write_text("fake core data 2")
            unrelated = tmppath / "other.log"
            unrelated.write_text("log data")

            cores = find_core_files([tmppath], binary_name="fbasecman")
            self.assertEqual(2, len(cores))
            core_names = [c.name for c in cores]
            self.assertIn("core.fbasecman.1234.5678", core_names)
            self.assertIn("core", core_names)

    def test_diagnose_crash_on_sigsegv(self):
        with TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            bin_path = tmppath / "fbasecman"
            bin_path.write_text("fake binary")

            diag = diagnose_crash(
                binary_path=bin_path,
                workdirs=[tmppath],
                returncode=139,
            )
            self.assertTrue(diag["is_crash"])
            self.assertEqual("SIGSEGV", diag["signal"])

    def test_diagnose_no_crash_on_clean_exit(self):
        with TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            bin_path = tmppath / "fbasecman"
            bin_path.write_text("fake binary")

            diag = diagnose_crash(
                binary_path=bin_path,
                workdirs=[tmppath],
                returncode=0,
            )
            self.assertFalse(diag["is_crash"])
            self.assertIsNone(diag["core_file"])


if __name__ == "__main__":
    unittest.main()
