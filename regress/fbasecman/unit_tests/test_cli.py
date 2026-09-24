import unittest
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from tools.cli import build_parser, main, _case_status, _read_last_failed, _write_last_failed
from tools.clean import run_clean


class CliImportTest(unittest.TestCase):
    def test_environment_commands_remain_registered(self):
        parser = build_parser()
        for command in ("setup", "clean", "status", "start", "restart", "stop"):
            args = parser.parse_args(["env", command])
            self.assertEqual("env", args.command)
            self.assertEqual(command, args.env_command)

    def test_regression_output_cleanup_preserves_stable_artifacts(self):
        parser = build_parser()
        args = parser.parse_args(["outout", "clean"])
        self.assertEqual("outout", args.command)
        self.assertEqual("clean", args.output_command)

        with TemporaryDirectory() as directory:
            root = Path(directory)
            regress_env = root / "output" / "env"
            regress_run = root / "output" / "runs" / "global_cache"
            stable_run = root / "output" / "stable" / "runs" / "run_1"
            regress_env.mkdir(parents=True)
            regress_run.mkdir(parents=True)
            stable_run.mkdir(parents=True)
            (stable_run / "report.txt").write_text("stable", encoding="utf-8")

            result = run_clean(root, output_only=True)

            self.assertFalse(regress_env.exists())
            self.assertFalse((root / "output" / "runs").exists())
            self.assertTrue((stable_run / "report.txt").exists())
            self.assertNotIn(str(root / "output" / "stable"), result.removed)

    def test_unknown_run_target_is_a_failure_for_ci(self):
        import sys
        from unittest.mock import patch

        with patch.object(sys, "argv", ["run.sh", "run", "missing_suite"]), redirect_stderr(StringIO()):
            self.assertEqual(2, main())

    def test_failed_is_accepted_as_run_target(self):
        args = build_parser().parse_args(["run", "failed"])
        self.assertEqual("failed", args.target)
        args = build_parser().parse_args(["run", "faild"])
        self.assertEqual("faild", args.target)

    def test_outstanding_is_registered_as_run_target(self):
        args = build_parser().parse_args(["run", "outstanding"])
        self.assertEqual("outstanding", args.target)

    def test_last_failed_roundtrip(self):
        import tools.cli as cli
        from unittest.mock import patch

        with TemporaryDirectory() as directory:
            state_path = Path(directory) / "last_failed.json"
            with patch.object(cli, "LAST_FAILED_PATH", state_path):
                _write_last_failed([
                    "ha_commands.set_node_write_idempotent",
                    "ha_commands.set_node_write_idempotent",
                    "handover.ha_replica_failure",
                ])
                self.assertEqual(
                    ["ha_commands.set_node_write_idempotent", "handover.ha_replica_failure"],
                    _read_last_failed())

    def test_case_status_reads_report(self):
        import tools.cli as cli
        from unittest.mock import patch

        with TemporaryDirectory() as directory:
            root = Path(directory)
            report = root / "output" / "runs" / "rw_toggle" / "sample" / "report.txt"
            report.parent.mkdir(parents=True)
            report.write_text("用例: rw_toggle.sample\n结论: FAIL\n", encoding="utf-8")
            with patch.object(cli, "ROOT_DIR", root):
                self.assertEqual("FAIL", _case_status("rw_toggle.sample"))
