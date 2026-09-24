"""Reusable fbasecman process lifecycle."""

import re
import shlex
import time
from pathlib import Path

from framework.clients.psql import build_psql_command


class FbasecmanProcessError(RuntimeError):
    pass


class FbasecmanProcess(object):
    def __init__(self, binary, postgres_dir, listen_port, prom_port, pid_file,
                 locks_dir, product_log, logs_dir, execute, trace,
                 port_is_free, sleep=time.sleep):
        self.binary = str(binary)
        self.postgres_dir = str(postgres_dir)
        self.listen_port = int(listen_port)
        self.prom_port = int(prom_port)
        self.pid_file = Path(pid_file)
        self.locks_dir = Path(locks_dir)
        self.product_log = Path(product_log)
        self.logs_dir = Path(logs_dir)
        self.execute = execute
        self.trace = trace
        self.port_is_free = port_is_free
        self.sleep = sleep
        self.active_conf = None

    def config_replacements(self, log_level):
        return [
            ('pid_file "/tmp/fbasecman.pid"', 'pid_file "%s"' % self.pid_file),
            ('locks_dir "/tmp/odyssey"', 'locks_dir "%s"' % self.locks_dir),
            ('ports "17432"', 'ports "%s"' % self.listen_port),
            ('promhttp_server_port 7777', 'promhttp_server_port %s' % self.prom_port),
            ('log_file "/home/postgres/fly_dev/fbasecman_dev_autotest/test/fbasecman/test_logs/extend_query/mmr_hint_pool.log"', 'log_file "%s"' % self.product_log),
            ('log_file "/home/postgres/fly_dev/fbasecman_dev_autotest/test/fbasecman/test_logs/extend_query/rep_hint_pool.log"', 'log_file "%s"' % self.product_log),
            ('log_min_messages "info"', 'log_min_messages "%s"' % log_level),
        ]

    def start(self, conf, ready_timeout=30.0, foreground=False, record=True,
              env=None):
        conf = Path(conf)
        self.stop(best_effort=True, record=False, conf=conf)
        if not self.port_is_free(self.listen_port):
            raise FbasecmanProcessError(
                "listen port %s is still in use after stop cleanup" % self.listen_port
            )
        if self.product_log.exists():
            self.product_log.unlink()
        command = [self.binary, str(conf)]
        if foreground:
            # The transfer document's second startup form uses daemonize=no
            # and shell backgrounding.  Keep that lifecycle in the adapter.
            command = ["bash", "-lc", "nohup %s > %s 2>&1 & echo $!" % (
                " ".join(shlex.quote(part) for part in command),
                shlex.quote(str(self.logs_dir / "fbasecman.foreground.log")),
            )]
        execute_kwargs = {
            "step_title": "启动 fbasecman",
            "check": False,
            "record": record,
        }
        if env is not None:
            execute_kwargs["env"] = env
        rc, output = self.execute(
            command, self.logs_dir / "fbasecman.start.log", **execute_kwargs)
        if rc != 0:
            raise FbasecmanProcessError(
                "fbasecman start failed rc=%s: %s" % (rc, output.strip())
            )
        self.active_conf = conf
        self.wait_ready(ready_timeout)

    def stop(self, best_effort=False, conf=None, record=True):
        conf = Path(conf or self.active_conf) if (conf or self.active_conf) else None
        if conf is None:
            return
        if not self.pid_file.exists():
            self.trace("[skip] stop fbasecman: pid file not found")
            self._force_cleanup(best_effort=best_effort, record=record)
            return
        rc, _ = self.execute(
            [self.binary, str(conf), "--stop"],
            self.logs_dir / "fbasecman.stop.log",
            check=False,
            step_title="停止 fbasecman",
            record=record,
        )
        self._force_cleanup(best_effort=True, record=record)
        self.active_conf = None
        if rc != 0 and not best_effort:
            raise FbasecmanProcessError("failed to stop fbasecman with rc=%s" % rc)

    def diagnose_crash(self, search_dirs=None, since_time=None, returncode=None):
        """Diagnose crash, core dump, and GDB backtrace for fbasecman."""
        try:
            from framework.execution.forensics import diagnose_crash
            dirs = list(search_dirs or [])
            if self.logs_dir:
                dirs.append(self.logs_dir)
                if self.logs_dir.parent:
                    dirs.append(self.logs_dir.parent)
            if self.locks_dir:
                dirs.append(self.locks_dir)
            dirs.extend([Path("/tmp"), Path.cwd()])
            valid_dirs = [Path(d).resolve() for d in dirs if d and Path(d).exists()]
            return diagnose_crash(
                binary_path=Path(self.binary),
                workdirs=valid_dirs,
                returncode=returncode,
                since_time=since_time,
            )
        except Exception as exc:
            return {"is_crash": False, "error": str(exc), "all_cores": [], "backtrace": None}

    def wait_ready(self, timeout=15.0):
        deadline = time.time() + timeout
        attempts = 0
        last_error = ""
        while time.time() < deadline:
            attempts += 1
            rc, output = self.execute(
                build_psql_command(
                    self.postgres_dir, "localhost", self.listen_port, "admin", "console",
                    "show global_prepared_statements_stats;",
                    footer=False, output_format="unaligned",
                ),
                self.logs_dir / ("wait_console_ready_%02d.log" % attempts),
                check=False,
                record=False,
            )
            if rc == 0:
                self.trace("[ready] console is ready after %d attempt(s)" % attempts)
                return
            last_error = output.strip()
            self.sleep(0.5)

        diag = self.diagnose_crash()
        crash_extra = ""
        if diag.get("is_crash"):
            sig = diag.get("signal") or "CRASH"
            core = diag.get("core_file") or "<none>"
            crash_extra = "\n🚨 检测到 fbasecman 异常崩溃 (Signal=%s, Core=%s)" % (sig, core)
            if diag.get("backtrace"):
                crash_extra += "\n堆栈摘要:\n%s" % diag["backtrace"]

        raise FbasecmanProcessError(
            "fbasecman console not ready within %.1fs: %s%s" % (timeout, last_error, crash_extra)
        )

    def _force_cleanup(self, best_effort, record=True, retries=8):
        for _ in range(max(1, retries)):
            self._kill_by_pid_file(best_effort, record)
            self._kill_by_port(best_effort, record)
            if not self.pid_file.exists() and self.port_is_free(self.listen_port):
                return
            self.sleep(0.5)
        if not self.port_is_free(self.listen_port) and not best_effort:
            raise FbasecmanProcessError(
                "listen port %s still busy after stop cleanup" % self.listen_port
            )

    def _kill_by_pid_file(self, best_effort, record):
        if not self.pid_file.exists():
            return
        pid_text = self.pid_file.read_text(encoding="utf-8", errors="replace").strip()
        if not pid_text.isdigit():
            if not best_effort:
                raise FbasecmanProcessError("invalid pid file content: %s" % pid_text)
            return
        probe_rc, _ = self.execute(
            ["kill", "-0", pid_text], self.logs_dir / ("pid_probe_%s.log" % pid_text),
            check=False, record=False,
        )
        if probe_rc != 0:
            return
        self.trace("[cleanup] kill residual fbasecman pid=%s" % pid_text)
        self.execute(
            ["kill", "-TERM", pid_text], self.logs_dir / ("kill_pidfile_%s.log" % pid_text),
            check=False, step_title="按 pid_file 清理残留 fbasecman 进程", record=record,
        )
        self.sleep(0.5)

    def _kill_by_port(self, best_effort, record):
        rc, output = self.execute(
            "ss -ltnp | grep ':%s ' || true" % self.listen_port,
            self.logs_dir / "fbasecman.port_probe.log", check=False, record=False,
        )
        if rc not in (0, 1):
            if not best_effort:
                raise FbasecmanProcessError(
                    "failed to probe listen port %s" % self.listen_port
                )
            return
        for pid in sorted(set(re.findall(r"pid=(\d+)", output))):
            self.trace("[cleanup] kill residual fbasecman pid=%s on port=%s" % (pid, self.listen_port))
            self.execute(
                ["kill", "-TERM", pid], self.logs_dir / ("kill_%s.log" % pid),
                check=False, step_title="清理残留 fbasecman 进程", record=record,
            )
        if output:
            self.sleep(0.5)
