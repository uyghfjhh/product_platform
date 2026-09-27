import datetime
import json
import tempfile
import unittest
from pathlib import Path
from xml.etree import ElementTree

from framework.artifacts import (ClusterRunLock, create_run_id, run_directory,
                                 write_run_summary)
from framework.errors import OperationError


class RunArtifactsTest(unittest.TestCase):
    def test_cluster_lock_rejects_a_second_runner(self):
        with tempfile.TemporaryDirectory() as root:
            with ClusterRunLock(root, "mac"):
                with self.assertRaises(OperationError):
                    with ClusterRunLock(root, "mac"):
                        pass

    def test_cluster_identity_lock_is_shared_by_different_worktrees(self):
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            identity = '[{"host":"127.0.0.1","port":15432,"data_dir":"/tmp/mac"}]'
            with ClusterRunLock(first, "mac", identity):
                with self.assertRaises(OperationError):
                    with ClusterRunLock(second, "mac", identity):
                        pass

    def test_summary_and_junit_capture_case_statuses(self):
        with tempfile.TemporaryDirectory() as root:
            run_id = create_run_id()
            run_dir = run_directory(root, "mac", run_id)
            now = datetime.datetime(2026, 7, 14, 16, 30, 0)
            records = [
                {"id": "mac.group.success", "status": "SUCCESS",
                 "duration_seconds": 0.1, "report": "output/runs/mac/x/success/report.txt",
                 "evidence": [], "reason": ""},
                {"id": "mac.group.blocked", "status": "BLOCKED",
                 "duration_seconds": 0.2, "report": "output/runs/mac/x/blocked/report.txt",
                 "evidence": [], "reason": "cluster unavailable"},
                {"id": "mac.group.failed", "status": "FAILED",
                 "duration_seconds": 0.3, "report": "output/runs/mac/x/failed/report.txt",
                 "evidence": [], "reason": "expected=1 actual=0"},
            ]
            payload = write_run_summary(
                run_dir, run_id, "mac", "mac.group", {"env_id": "env1"},
                {"fbase_mac": {}}, records, now, now + datetime.timedelta(seconds=1))

            self.assertEqual(payload["counts"], {
                "success": 1, "failed": 1, "blocked": 1})
            self.assertEqual(json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))["run_id"], run_id)
            suite = ElementTree.parse(str(run_dir / "junit.xml")).getroot()
            self.assertEqual(suite.attrib["tests"], "3")
            self.assertEqual(suite.attrib["failures"], "1")
            self.assertEqual(suite.attrib["skipped"], "1")
            self.assertEqual(len(suite.findall("testcase/skipped")), 1)
            self.assertEqual(len(suite.findall("testcase/failure")), 1)
