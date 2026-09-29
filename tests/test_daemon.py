"""Platform tests for the generic ManagedDaemon lifecycle kernel."""

import os
import tempfile
import time
import unittest
from pathlib import Path

from platform_regress.execution.daemon import ManagedDaemon, ManagedDaemonError


def make_daemon(root, execute=None, port_free=None, ready_probe=None, **kwargs):
    return ManagedDaemon(
        name="testdaemon",
        binary="/bin/testdaemon",
        listen_port=6432,
        pid_file=root / "testdaemon.pid",
        product_log=root / "testdaemon.log",
        logs_dir=root / "logs",
        execute=execute or (lambda *a, **k: (0, "")),
        trace=kwargs.pop("trace", lambda m: None),
        port_is_free=port_free or (lambda p: True),
        ready_probe=ready_probe or (lambda: ["/bin/probe", "ready"]),
        sleep=lambda s: None,
        **kwargs,
    )


class ManagedDaemonTest(unittest.TestCase):
    def test_start_failure_raises_error(self):
        with tempfile.TemporaryDirectory() as directory:
            daemon = make_daemon(
                Path(directory),
                execute=lambda *a, **k: (1, "address already in use"),
            )
            with self.assertRaisesRegex(ManagedDaemonError, "testdaemon start failed rc=1"):
                daemon.start(Path(directory) / "test.conf")

    def test_start_passes_env_and_records_active_conf(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            calls = []

            def execute(command, logfile, **kwargs):
                calls.append((command, kwargs))
                return (0, "")

            daemon = make_daemon(root, execute=execute)
            conf = root / "daemon.conf"
            daemon.start(conf, env={"MYENV": "1"})

            self.assertEqual(conf, daemon.active_conf)
            starts = [
                c for c in calls
                if isinstance(c[0], list) and str(conf) in c[0]
            ]
            self.assertEqual(1, len(starts))
            self.assertEqual(["/bin/testdaemon", str(conf)], starts[0][0])
            self.assertEqual({"MYENV": "1"}, starts[0][1]["env"])

    def test_foreground_wraps_command_in_nohup(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            calls = []

            def execute(command, logfile, **kwargs):
                calls.append(command)
                return (0, "")

            daemon = make_daemon(root, execute=execute)
            daemon.start(root / "daemon.conf", foreground=True)

            starts = [c for c in calls if isinstance(c, list) and c[0] == "bash"]
            self.assertEqual(1, len(starts))
            self.assertIn("nohup /bin/testdaemon", starts[0][2])
            self.assertIn("testdaemon.foreground.log", starts[0][2])

    def test_wait_ready_retries_until_probe_succeeds(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            results = [(1, "starting"), (0, "ready")]
            probes = []

            def execute(command, logfile, **kwargs):
                probes.append((command, logfile.name))
                return results.pop(0)

            daemon = make_daemon(root, execute=execute)
            daemon.wait_ready(5)

            self.assertEqual(2, len(probes))
            self.assertEqual(["/bin/probe", "ready"], probes[0][0])
            self.assertEqual("wait_ready_ready_01.log", probes[0][1])

    def test_wait_ready_timeout_raises(self):
        with tempfile.TemporaryDirectory() as directory:
            daemon = make_daemon(
                Path(directory),
                execute=lambda *a, **k: (1, "not up"),
            )
            with self.assertRaisesRegex(ManagedDaemonError, "not ready within"):
                daemon.wait_ready(0.01)

    def test_stop_tolerates_daemon_exit_after_sigint(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            daemon = make_daemon(
                root, execute=lambda *a, **k: (1, "race"))
            # pid file absent -> stop skips the stop argv and just cleans up.
            daemon.stop(best_effort=False, conf=root / "c.conf")

    def test_stop_raises_when_port_still_busy(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "testdaemon.pid").write_text("123")
            daemon = make_daemon(
                root,
                execute=lambda *a, **k: (1, "still running"),
                port_free=lambda p: False,
            )
            with self.assertRaisesRegex(ManagedDaemonError, "failed to stop testdaemon"):
                daemon.stop(conf=root / "c.conf")

    def test_start_argv_hooks_are_overridable(self):
        class Custom(ManagedDaemon):
            def start_argv(self, conf):
                return [self.binary, "--config", str(conf), "--daemon"]

            def stop_argv(self, conf):
                return [self.binary, "shutdown"]

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            calls = []

            def execute(command, logfile, **kwargs):
                calls.append(command)
                return (0, "")

            daemon = make_daemon(root, execute=execute)
            daemon.__class__ = Custom
            daemon.start(root / "c.conf")
            starts = [c for c in calls if isinstance(c, list) and str(root / "c.conf") in c]
            self.assertEqual(
                ["/bin/testdaemon", "--config", str(root / "c.conf"), "--daemon"],
                starts[0],
            )


    def test_recycled_pid_in_pid_file_is_not_killed(self):
        """A stale pid file pointing at a reused PID must not be SIGTERMed."""
        import os
        import subprocess
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sleeper = subprocess.Popen(["sleep", "30"])
            try:
                (root / "testdaemon.pid").write_text(str(sleeper.pid))
                kills = []

                def execute(command, logfile, **kwargs):
                    kills.append(command)
                    if isinstance(command, list) and command[:2] == ["kill", "-0"]:
                        return (0, "")
                    return (0, "")

                daemon = make_daemon(root, execute=execute)
                daemon._force_cleanup(best_effort=True)
                terms = [c for c in kills
                         if isinstance(c, list) and c[:2] == ["kill", "-TERM"]]
                self.assertEqual([], terms)
                self.assertFalse((root / "testdaemon.pid").exists())
            finally:
                sleeper.kill()
                sleeper.wait()

    def test_pid_file_owned_by_daemon_is_killed(self):
        """A live process whose cmdline references the binary is ours."""
        import subprocess
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            daemon = make_daemon(root)
            fake = subprocess.Popen(
                ["bash", "-c", "exec -a /bin/testdaemon sleep 30"])
            try:
                # cmdline stays empty until the child's exec lands; poll for it.
                owned = False
                for _ in range(50):
                    if daemon._owns_pid(str(fake.pid)):
                        owned = True
                        break
                    time.sleep(0.05)
                self.assertTrue(owned)
                self.assertFalse(daemon._owns_pid(str(os.getpid())))
            finally:
                fake.kill()
                fake.wait()


if __name__ == "__main__":
    unittest.main()
