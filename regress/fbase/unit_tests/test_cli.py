import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from framework.cli import build_parser, do_env, do_output, do_report, do_show


class CliTest(unittest.TestCase):
    def test_setup_command(self):
        args = build_parser().parse_args(["env", "setup", "mac"])
        self.assertEqual((args.command, args.env_command, args.cluster), ("env", "setup", "mac"))

    def test_reload_command(self):
        args = build_parser().parse_args(["env", "reload", "mac"])
        self.assertEqual(args.env_command, "reload")

    def test_mutating_env_command_uses_cluster_lock(self):
        config = SimpleNamespace(root=Path("/tmp/test-root"))
        args = SimpleNamespace(env_command="restart", cluster="mac")
        manager = SimpleNamespace(restart=lambda: None)
        with patch("framework.cli.EnvironmentManager", return_value=manager), \
                patch("framework.cli.ClusterRunLock") as lock:
            self.assertEqual(do_env(config, args), 0)
        lock.assert_called_once_with(config.root, "mac")
        self.assertTrue(lock.return_value.__enter__.called)

    def test_doctor_target(self):
        args = build_parser().parse_args([
            "doctor", "--cluster", "mac", "mac.tde"
        ])
        self.assertEqual(args.target, "mac.tde")

    def test_run_uses_cluster_and_target_positionally(self):
        args = build_parser().parse_args([
            "run", "mac", "sso_system_privileges"
        ])
        self.assertEqual(args.cluster, "mac")
        self.assertEqual(args.target, "sso_system_privileges")

    def test_run_target_is_optional(self):
        args = build_parser().parse_args(["run", "mac"])
        self.assertEqual(args.cluster, "mac")
        self.assertIsNone(args.target)
        self.assertFalse(args.enable_run_id)

    def test_run_can_explicitly_enable_run_id(self):
        args = build_parser().parse_args(["run", "mmr", "--enable-run-id"])
        self.assertTrue(args.enable_run_id)

    def test_run_all_includes_default_disabled_cases(self):
        args = build_parser().parse_args(["run", "mmr", "--all"])
        self.assertTrue(args.all)

    def test_run_longtime_selects_only_longtime_cases(self):
        args = build_parser().parse_args(["run", "mmr", "--longtime"])
        self.assertTrue(args.longtime)

    def test_run_all_and_longtime_are_mutually_exclusive(self):
        with self.assertRaises(SystemExit):
            build_parser().parse_args(["run", "mmr", "--all", "--longtime"])

    def test_output_clean_accepts_an_optional_cluster(self):
        args = build_parser().parse_args(["output", "clean", "mac"])
        self.assertEqual((args.command, args.output_command, args.cluster),
                         ("output", "clean", "mac"))

    def test_output_clean_preserves_environment_state(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            (root / "output" / "runs" / "mac").mkdir(parents=True)
            (root / "output" / "mac").mkdir(parents=True)
            environment = root / "output" / "envs" / "mac"
            environment.mkdir(parents=True)
            (environment / "state.yaml").write_text("state: running\n", encoding="utf-8")
            do_output(SimpleNamespace(root=root),
                      SimpleNamespace(cluster="mac"))
            self.assertFalse((root / "output" / "runs" / "mac").exists())
            self.assertFalse((root / "output" / "mac").exists())
            self.assertTrue((environment / "state.yaml").is_file())

    def test_output_clean_uses_cluster_lock(self):
        with tempfile.TemporaryDirectory() as root:
            config = SimpleNamespace(root=Path(root))
            args = SimpleNamespace(cluster="mac")
            with patch("framework.cli.ClusterRunLock") as lock:
                self.assertEqual(do_output(config, args), 0)
            lock.assert_called_once_with(config.root, "mac")

    def test_report_reads_case_results_from_run_summaries(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            summary = root / "output" / "runs" / "mac" / "run_test" / "summary.json"
            summary.parent.mkdir(parents=True)
            summary.write_text(json.dumps({
                "run_id": "run_test", "cases": [{
                    "status": "SUCCESS", "report": "output/runs/mac/run_test/mac/case/report.txt",
                }],
            }), encoding="utf-8")
            output = io.StringIO()
            with patch("framework.cli.ROOT", root), redirect_stdout(output):
                self.assertEqual(do_report(), 0)
            self.assertIn("SUCCESS", output.getvalue())
            self.assertIn("run_test", output.getvalue())

    def test_show_longtime_lists_only_manual_long_cases(self):
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(do_show("longtime"), 0)
        rendered = output.getvalue()
        self.assertIn("longtime cases:", rendered)
        self.assertIn("mmr.node_management.join_group", rendered)
        self.assertIn("mmr.node_management.multi_database_three_node_join", rendered)
        self.assertIn("mmr.node_management.online_join_data_retry", rendered)
        self.assertIn("./run.sh run mmr", rendered)
