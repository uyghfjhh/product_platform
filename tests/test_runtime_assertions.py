"""Platform tests for the shared assertion helpers on ``CaseRuntime``."""

import tempfile
import unittest
from pathlib import Path

from platform_regress.clients.psql import parse_expanded_rows
from platform_regress.evidence.backup import (
    backup_content_matches, backup_dir_path, backup_files, created_backups,
    snapshot_backup,
)
from platform_regress.runtime import (
    CaseRuntime, CaseRuntimeFailure, EnvironmentRef, RegressionContext,
)


class FakeCase(object):
    name = "demo"
    summary = "demo case"
    suite_name = "demo_suite"

    @property
    def target(self):
        return "demo_suite.demo"


def make_runtime(root):
    context = RegressionContext(
        environment=EnvironmentRef(
            id="env", product_id="demo", deployment_config=root / "d.yaml",
            deployment_target="local", host="127.0.0.1", port=5432),
        output_dir=root / "output",
        profile_id="local",
    )
    runtime = CaseRuntime(root, FakeCase(), platform_context=context)
    runtime.write_report = lambda *a, **k: None  # avoid rendering on tmp dirs
    return runtime


class AssertedCommandTest(unittest.TestCase):
    def test_passes_on_first_attempt(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = make_runtime(Path(directory))
            calls = []

            def fake_run(command, logfile, cwd=None, env=None, echo=False):
                calls.append(Path(logfile).name)
                return type("R", (), {"command": " ".join(command),
                                      "returncode": 0, "output": "ok"})

            runtime.workdir.mkdir(parents=True, exist_ok=True)
            runtime.logs_dir.mkdir(parents=True, exist_ok=True)
            import platform_regress.runtime as rt
            original = rt.run_logged_command
            rt.run_logged_command = fake_run
            try:
                output, result = runtime.asserted_command(
                    ["/bin/true"], "check", "expected",
                    lambda r, o, a, e: (True, "actual-ok"),
                    log_stem="demo")
            finally:
                rt.run_logged_command = original
            self.assertEqual("ok", output)
            self.assertEqual(["demo_01_01.log"], calls)

    def test_retries_until_deadline_then_raises(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = make_runtime(Path(directory))
            attempts = []

            def fake_run(command, logfile, cwd=None, env=None, echo=False):
                attempts.append(1)
                return type("R", (), {"command": "x", "returncode": 1,
                                      "output": "not yet"})

            runtime.workdir.mkdir(parents=True, exist_ok=True)
            runtime.logs_dir.mkdir(parents=True, exist_ok=True)
            import platform_regress.runtime as rt
            original = rt.run_logged_command
            rt.run_logged_command = fake_run
            try:
                with self.assertRaises(CaseRuntimeFailure) as caught:
                    runtime.asserted_command(
                        ["/bin/false"], "check", "expected",
                        lambda r, o, a, e: (False, "rc=%s" % r.returncode),
                        retry_timeout=0.3, interval=0.01, log_stem="demo")
            finally:
                rt.run_logged_command = original
            self.assertIn("check: rc=1", str(caught.exception))
            self.assertGreater(len(attempts), 1)

    def test_custom_failure_message(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = make_runtime(Path(directory))
            runtime.workdir.mkdir(parents=True, exist_ok=True)
            runtime.logs_dir.mkdir(parents=True, exist_ok=True)
            import platform_regress.runtime as rt
            original = rt.run_logged_command
            rt.run_logged_command = lambda *a, **k: type(
                "R", (), {"command": "x", "returncode": 7, "output": ""})
            try:
                with self.assertRaisesRegex(CaseRuntimeFailure, "rc=7 only"):
                    runtime.asserted_command(
                        ["/bin/false"], "t", "e",
                        lambda r, o, a, e: (False, ""),
                        failure=lambda t, a, r: "rc=7 only")
            finally:
                rt.run_logged_command = original

    def test_judge_receives_attempt_and_elapsed(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = make_runtime(Path(directory))
            runtime.workdir.mkdir(parents=True, exist_ok=True)
            runtime.logs_dir.mkdir(parents=True, exist_ok=True)
            import platform_regress.runtime as rt
            original = rt.run_logged_command
            rt.run_logged_command = lambda *a, **k: type(
                "R", (), {"command": "x", "returncode": 0, "output": "o"})
            seen = []
            try:
                runtime.asserted_command(
                    ["/bin/true"], "t", "e",
                    lambda r, o, a, e: (seen.append((a, e)) or True, "a"))
            finally:
                rt.run_logged_command = original
            self.assertEqual(1, seen[0][0])
            self.assertGreaterEqual(seen[0][1], 0.0)


class FileDiffTest(unittest.TestCase):
    def test_file_diff_runs_diff_u(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = make_runtime(root)
            (root / "a.txt").write_text("line1\n")
            (root / "b.txt").write_text("line2\n")
            result = runtime.file_diff(root / "a.txt", root / "b.txt",
                                       "diff.log")
            self.assertEqual(1, result.returncode)
            self.assertIn("-line1", result.output)
            self.assertIn("+line2", result.output)


class ParseExpandedRowsTest(unittest.TestCase):
    OUTPUT = (
        "-[ RECORD 1 ]---\n"
        "node_name | pg_220\n"
        "state     | active\n"
        "-[ RECORD 2 ]---\n"
        "node_name | pg_240\n"
        "state     | parted\n"
    )

    def test_indexes_rows_by_key_column(self):
        rows = parse_expanded_rows(self.OUTPUT, "node_name")
        self.assertEqual({"node_name": "pg_220", "state": "active"},
                         rows["pg_220"])
        self.assertEqual({"node_name": "pg_240", "state": "parted"},
                         rows["pg_240"])

    def test_lowercase_field_pattern_only(self):
        output = ("-[ RECORD 1 ]---\n"
                  "CamelCase | skipped\n"
                  "good_name | kept\n")
        rows = parse_expanded_rows(output, "good_name")
        self.assertIn("kept", rows)
        self.assertEqual({"good_name": "kept"}, rows["kept"])


class BackupPrimitivesTest(unittest.TestCase):
    def test_snapshot_and_created_backups(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "fbasecman.conf"
            config.write_text("conf-v1\n")
            backup_dir = backup_dir_path(config)
            self.assertEqual(root / "conf-backup", backup_dir)
            checkpoint = snapshot_backup(config)
            self.assertEqual((), tuple(sorted(checkpoint.files)))

            backup_dir.mkdir(parents=True)
            (backup_dir / "conf.bak.1").write_bytes(b"conf-v1\n")
            created, current = created_backups(checkpoint, backup_dir)
            self.assertEqual(["conf.bak.1"], created)
            self.assertTrue(
                backup_content_matches(checkpoint, backup_dir, "conf.bak.1"))

    def test_missing_backup_dir_yields_empty_sets(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "c.conf"
            config.write_text("x")
            checkpoint = snapshot_backup(config, backup_dir=root / "none")
            created, current = created_backups(checkpoint)
            self.assertEqual(([], set()), (created, current))
            self.assertEqual(set(), backup_files(root / "none"))


if __name__ == "__main__":
    unittest.main()
