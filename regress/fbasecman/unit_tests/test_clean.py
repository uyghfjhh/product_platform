import tempfile
import unittest
from pathlib import Path

from tools.clean import CleanResult, prune_run_artifacts, run_clean


class TestClean(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_prune_run_artifacts_deletes_bak_files(self):
        runs_dir = self.root / "output" / "runs" / "suite1" / "case1"
        runs_dir.mkdir(parents=True)
        bak_file = runs_dir / "fbasecman.log_bak_2026-01-01"
        bak_file.write_text("old log dump" * 100)

        result = CleanResult()
        prune_run_artifacts(self.root / "output" / "runs", max_file_size_mb=1.0, result=result)

        self.assertFalse(bak_file.exists())
        self.assertGreater(result.reclaimed_bytes, 0)
        self.assertEqual(len(result.pruned), 1)
        self.assertEqual(result.pruned[0]["action"], "deleted_backup")

    def test_prune_run_artifacts_truncates_large_log(self):
        runs_dir = self.root / "output" / "runs" / "suite2" / "case2"
        runs_dir.mkdir(parents=True)
        big_log = runs_dir / "test.log"
        # Write 1.5MB of lines
        line = "INFO 2026-01-01 12:00:00 sample regression log line content\n"
        big_log.write_text(line * 25000)
        original_size = big_log.stat().st_size
        self.assertGreater(original_size, 1024 * 1024)

        result = CleanResult()
        # Threshold: 0.5 MB
        prune_run_artifacts(
            self.root / "output" / "runs",
            max_file_size_mb=0.5,
            keep_head_lines=50,
            keep_tail_lines=100,
            result=result,
        )

        self.assertTrue(big_log.exists())
        new_size = big_log.stat().st_size
        self.assertLess(new_size, original_size)
        content = big_log.read_text(encoding="utf-8")
        self.assertIn("TRUNCATED OVERSIZED LOG", content)
        self.assertEqual(result.pruned[0]["action"], "truncated")
        self.assertEqual(result.reclaimed_bytes, original_size - new_size)

    def test_run_clean_prune_logs_flag(self):
        runs_dir = self.root / "output" / "runs" / "suite3" / "case3"
        runs_dir.mkdir(parents=True)
        bak = runs_dir / "old.log.bak"
        bak.write_text("hello")
        res = run_clean(self.root, prune_logs=True)
        self.assertFalse(bak.exists())


if __name__ == "__main__":
    unittest.main()
