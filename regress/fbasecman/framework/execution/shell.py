"""Logged shell and remote command execution."""

import os
import shlex
import signal
import subprocess
import sys
import time


def quote_arguments(items):
    return " ".join(shlex.quote(str(item)) for item in items)


class ShellResult(object):
    def __init__(self, command, returncode, stdout, stderr, duration=0, timed_out=False):
        self.command = command
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.duration = duration
        self.timed_out = timed_out


class ShellCommandError(RuntimeError):
    def __init__(self, result):
        super().__init__("command failed (%s): %s" % (result.returncode, result.command))
        self.result = result


class ShellTimeoutError(ShellCommandError):
    def __init__(self, result, timeout):
        RuntimeError.__init__(self, "command timed out after %ss: %s" % (timeout, result.command))
        self.result = result
        self.timeout = timeout


class LoggedShellRunner(object):
    def __init__(self, log_dir, verbose=True, default_timeout=60, heartbeat_interval=15):
        self.log_dir = log_dir
        self.verbose = verbose
        self.default_timeout = float(default_timeout)
        self.heartbeat_interval = float(heartbeat_interval)
        self.log_dir.mkdir(parents=True, exist_ok=True)

    def run(self, command, log_name, check=True, display_command=None,
            timeout=None, input_text=None):
        timeout = self.default_timeout if timeout is None else float(timeout)
        command_text = quote_arguments(command) if isinstance(command, (list, tuple)) else command
        display = display_command or command_text
        if self.verbose:
            print("[cmd] %s" % display, flush=True)
        started = time.monotonic()
        process = subprocess.Popen(
            command, shell=isinstance(command, str), stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, stdin=subprocess.PIPE if input_text is not None else None,
            universal_newlines=True, start_new_session=True,
        )
        stdout, stderr, timed_out = self._communicate(
            process, input_text, timeout, display, started,
        )
        duration = time.monotonic() - started
        result = ShellResult(
            command=display,
            returncode=process.returncode,
            stdout=stdout or "",
            stderr=stderr or "",
            duration=duration,
            timed_out=timed_out,
        )
        self._echo(result)
        self._write_log(log_name, result)
        if timed_out:
            raise ShellTimeoutError(result, timeout)
        if check and result.returncode != 0:
            raise ShellCommandError(result)
        return result

    def run_remote(self, user, host, remote_script, log_name, check=True, timeout=None):
        connect_timeout = max(1, min(10, int(timeout or self.default_timeout)))
        command = [
            "ssh", "-F", "/dev/null", "-o", "BatchMode=yes",
            "-o", "ConnectTimeout=%s" % connect_timeout,
            "-o", "ServerAliveInterval=10", "-o", "ServerAliveCountMax=3",
            "%s@%s" % (user, host), "bash", "-se",
        ]
        display = "ssh -F /dev/null %s@%s <remote script> (log: %s)" % (
            user, host, log_name
        )
        return self.run(
            command, log_name, check=check, display_command=display,
            timeout=timeout, input_text=remote_script,
        )

    def _communicate(self, process, input_text, timeout, display, started):
        deadline = started + timeout
        pending_input = input_text
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self._terminate(process)
                stdout, stderr = process.communicate()
                return stdout, stderr, True
            try:
                stdout, stderr = process.communicate(
                    input=pending_input,
                    timeout=min(remaining, self.heartbeat_interval),
                )
                return stdout, stderr, False
            except subprocess.TimeoutExpired:
                pending_input = None
                if self.verbose:
                    elapsed = int(time.monotonic() - started)
                    print("[cmd] still running after %ss: %s" % (elapsed, display), flush=True)

    @staticmethod
    def _terminate(process):
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGTERM)
            process.wait(timeout=3)
        except (OSError, subprocess.TimeoutExpired):
            try:
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
            except OSError:
                pass

    def _echo(self, result):
        if self.verbose and result.stdout:
            sys.stdout.write(result.stdout)
            sys.stdout.flush()
        if self.verbose and result.stderr:
            sys.stderr.write(result.stderr)
            sys.stderr.flush()

    def _write_log(self, log_name, result):
        (self.log_dir / log_name).write_text(
            "\n".join([
                "$ %s" % result.command,
                "", "=== STDOUT ===", result.stdout,
                "", "=== STDERR ===", result.stderr,
                "", "=== DURATION: %.3fs ===" % result.duration,
                "=== TIMED OUT: %s ===" % ("yes" if result.timed_out else "no"),
                "", "=== RETURN CODE: %s ===" % result.returncode,
            ]),
            encoding="utf-8",
        )
