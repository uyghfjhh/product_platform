import os
import tempfile
import unittest
from pathlib import Path

from framework.evidence.log_window import (
    LocalLogWindow, parse_remote_log_snapshot, remote_collect_script, remote_snapshot_script,
)


class LocalLogWindowTest(unittest.TestCase):
    def test_collects_only_content_appended_after_mark(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "fbasecman.log"
            path.write_text("old\n", encoding="utf-8")
            window = LocalLogWindow()
            marks = window.mark([path])
            with path.open("a", encoding="utf-8") as handle:
                handle.write("new cache hit\n")
            captured = window.collect(marks)
        self.assertEqual(captured[str(path)], "new cache hit\n")
        self.assertEqual(window.matching_lines(captured, ["cache hit"]), [(str(path), "new cache hit")])

    def test_collects_replaced_file_from_its_beginning(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "postgresql.log"
            path.write_text("old\n", encoding="utf-8")
            window = LocalLogWindow()
            marks = window.mark([path])
            os.rename(str(path), str(Path(tmp) / "postgresql.log.1"))
            path.write_text("new after rotation\n", encoding="utf-8")
            captured = window.collect(marks)
        self.assertEqual(captured[str(path)], "new after rotation\n")

    def test_parses_remote_snapshot(self):
        marks = parse_remote_log_snapshot("/pg/a.log\t11\t12\ninvalid\n/pg/b.log\t13\t14\n")
        self.assertEqual(marks["/pg/a.log"].inode, 11)
        self.assertEqual(marks["/pg/b.log"].size, 14)

    def test_remote_scripts_keep_inode_cursor_for_rotation(self):
        marks = parse_remote_log_snapshot("/pg/a.log\t11\t12\n")
        snapshot = remote_snapshot_script(["/pg"])
        collect = remote_collect_script(["/pg"], marks)
        self.assertIn("find \"$dir\" -type f", snapshot)
        self.assertIn("old_size[$inode]=$size", collect)
        self.assertIn("[ \"$size\" -gt \"$offset\" ] || continue", collect)
        self.assertIn("tail -c +$((offset + 1))", collect)
