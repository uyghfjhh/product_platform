"""Generic daemon lifecycle: start/stop argv hooks, ready probe, pid/port cleanup.

Products inject a ready probe callable and any product-specific argv/log
rendering; the kernel owns pre-start cleanup, port checks, the foreground
``nohup`` wrapper, readiness polling with crash forensics, ``--stop``
termination, and residual pid-file/port cleanup.
"""

import re
import shlex
import time
from pathlib import Path

from platform_regress.execution import forensics


class ManagedDaemonError(RuntimeError):
    pass


class ManagedDaemon(object):
    def __init__(self, *, name, binary, listen_port, pid_file, product_log,
                 logs_dir, execute, trace, port_is_free, ready_probe,
                 ready_label="ready", forensics_dirs=(), sleep=time.sleep):
        self.name = name
        self.binary = str(binary)
        self.listen_port = int(listen_port)
        self.pid_file = Path(pid_file)
        self.product_log = Path(product_log)
        self.logs_dir = Path(logs_dir)
        self.execute = execute
        self.trace = trace
        self.port_is_free = port_is_free
        self.ready_probe = ready_probe
        self.ready_label = ready_label
        self.forensics_dirs = tuple(forensics_dirs)
        self.sleep = sleep
        self.active_conf = None

    def start_argv(self, conf):
        return [self.binary, str(conf)]

    def stop_argv(self, conf):
        return [self.binary, str(conf), "--stop"]

    def foreground_argv(self, conf):
        # Some startup forms use daemonize=no and shell backgrounding.
        command = self.start_argv(conf)
        return ["bash", "-lc", "nohup %s > %s 2>&1 & echo $!" % (
            " ".join(shlex.quote(part) for part in command),
            shlex.quote(str(self.logs_dir / ("%s.foreground.log" % self.name))),
        )]

    def start(self, conf, ready_timeout=30.0, foreground=False, record=True,
              env=None):
        conf = Path(conf)
        self.stop(best_effort=True, record=False, conf=conf)
        if not self.port_is_free(self.listen_port):
            raise ManagedDaemonError(
                "listen port %s is still in use after stop cleanup" % self.listen_port
            )
        if self.product_log.exists():
            self.product_log.unlink()
        command = self.foreground_argv(conf) if foreground else self.start_argv(conf)
        execute_kwargs = {
            "step_title": "启动 %s" % self.name,
            "check": False,
            "record": record,
        }
        if env is not None:
            execute_kwargs["env"] = env
        rc, output = self.execute(
            command, self.logs_dir / ("%s.start.log" % self.name), **execute_kwargs)
        if rc != 0:
            raise ManagedDaemonError(
                "%s start failed rc=%s: %s" % (self.name, rc, output.strip())
            )
        self.active_conf = conf
        self.wait_ready(ready_timeout)

    def stop(self, best_effort=False, conf=None, record=True):
        conf = Path(conf or self.active_conf) if (conf or self.active_conf) else None
        if conf is None:
            return
        if not self.pid_file.exists():
            self.trace("[skip] stop %s: pid file not found" % self.name)
            self._force_cleanup(best_effort=best_effort, record=record)
            return
        rc, _ = self.execute(
            self.stop_argv(conf),
            self.logs_dir / ("%s.stop.log" % self.name),
            check=False,
            step_title="停止 %s" % self.name,
            record=record,
        )
        self._force_cleanup(best_effort=True, record=record)
        self.active_conf = None
        # The product stop command can race with its daemon's final pid-file
        # cleanup and return 1 after SIGINT has already shut the service down.
        # Treat that as success when the pid file is gone and the listen port
        # is free; report an error only when the service is still alive.
        if rc != 0 and not best_effort and (
            self.pid_file.exists() or not self.port_is_free(self.listen_port)
        ):
            raise ManagedDaemonError("failed to stop %s with rc=%s" % (self.name, rc))

    def diagnose_crash(self, search_dirs=None, since_time=None, returncode=None):
        """Diagnose crash, core dump, and GDB backtrace for the daemon."""
        try:
            dirs = list(search_dirs or [])
            if self.logs_dir:
                dirs.append(self.logs_dir)
                if self.logs_dir.parent:
                    dirs.append(self.logs_dir.parent)
            dirs.extend(Path(d) for d in self.forensics_dirs)
            dirs.extend([Path("/tmp"), Path.cwd()])
            valid_dirs = [Path(d).resolve() for d in dirs if d and Path(d).exists()]
            return forensics.diagnose_crash(
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
                self.ready_probe(),
                self.logs_dir / ("wait_%s_ready_%02d.log" % (self.ready_label, attempts)),
                check=False,
                record=False,
            )
            if rc == 0:
                self.trace("[ready] %s is ready after %d attempt(s)" % (self.ready_label, attempts))
                return
            last_error = output.strip()
            self.sleep(0.5)

        diag = self.diagnose_crash()
        crash_extra = ""
        if diag.get("is_crash"):
            sig = diag.get("signal") or "CRASH"
            core = diag.get("core_file") or "<none>"
            crash_extra = "\n🚨 检测到 %s 异常崩溃 (Signal=%s, Core=%s)" % (self.name, sig, core)
            if diag.get("backtrace"):
                crash_extra += "\n堆栈摘要:\n%s" % diag["backtrace"]

        raise ManagedDaemonError(
            "%s %s not ready within %.1fs: %s%s" % (
                self.name, self.ready_label, timeout, last_error, crash_extra)
        )

    def _force_cleanup(self, best_effort, record=True, retries=8):
        for _ in range(max(1, retries)):
            self._kill_by_pid_file(best_effort, record)
            self._kill_by_port(best_effort, record)
            if not self.pid_file.exists() and self.port_is_free(self.listen_port):
                return
            self.sleep(0.5)
        if not self.port_is_free(self.listen_port) and not best_effort:
            raise ManagedDaemonError(
                "listen port %s still busy after stop cleanup" % self.listen_port
            )

    def _kill_by_pid_file(self, best_effort, record):
        if not self.pid_file.exists():
            return
        pid_text = self.pid_file.read_text(encoding="utf-8", errors="replace").strip()
        if not pid_text.isdigit():
            if not best_effort:
                raise ManagedDaemonError("invalid pid file content: %s" % pid_text)
            return
        probe_rc, _ = self.execute(
            ["kill", "-0", pid_text], self.logs_dir / ("pid_probe_%s.log" % pid_text),
            check=False, record=False,
        )
        if probe_rc != 0:
            # A daemon may remove itself before the product stop command
            # finishes waiting.  Do not let the stale pid file make a later
            # stop look unsuccessful when the process is already gone.
            try:
                self.pid_file.unlink()
            except OSError:
                pass
            return
        self.trace("[cleanup] kill residual %s pid=%s" % (self.name, pid_text))
        self.execute(
            ["kill", "-TERM", pid_text], self.logs_dir / ("kill_pidfile_%s.log" % pid_text),
            check=False, step_title="按 pid_file 清理残留 %s 进程" % self.name, record=record,
        )
        self.sleep(0.5)

    def _kill_by_port(self, best_effort, record):
        rc, output = self.execute(
            "ss -ltnp | grep ':%s ' || true" % self.listen_port,
            self.logs_dir / ("%s.port_probe.log" % self.name), check=False, record=False,
        )
        if rc not in (0, 1):
            if not best_effort:
                raise ManagedDaemonError(
                    "failed to probe listen port %s" % self.listen_port
                )
            return
        for pid in sorted(set(re.findall(r"pid=(\d+)", output))):
            self.trace("[cleanup] kill residual %s pid=%s on port=%s" % (self.name, pid, self.listen_port))
            self.execute(
                ["kill", "-TERM", pid], self.logs_dir / ("kill_%s.log" % pid),
                check=False, step_title="清理残留 %s 进程" % self.name, record=record,
            )
        if output:
            self.sleep(0.5)
