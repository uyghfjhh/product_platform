from __future__ import annotations

import json
import os
import re
import shlex
import signal
import socket
import subprocess
import threading
import time
from concurrent.futures import Future
from concurrent.futures import TimeoutError as FutureTimeout
from pathlib import Path
from typing import TYPE_CHECKING

from .contracts import Cancelled, CommandResult

if TYPE_CHECKING:
    from .engine import CaseContext


_SECRET_ASSIGNMENT = re.compile(r"^([^=]*(?:password|passwd|secret|token|api_key)[^=]*)=(.*)$", re.IGNORECASE)
_SECRET_DSN = re.compile(r"(?i)(password\s*=\s*)(?:'[^']*'|\"[^\"]*\"|\S+)")
_SECRET_FLAGS = {"--password", "--passwd", "--secret", "--token", "--api-key"}


def safe_argv(argv: list[str]) -> list[str]:
    rendered = []
    hide_next = False
    for value in argv:
        if hide_next:
            rendered.append("<redacted>")
            hide_next = False
            continue
        if value.lower() in _SECRET_FLAGS:
            rendered.append(value)
            hide_next = True
            continue
        assignment = _SECRET_ASSIGNMENT.match(value)
        if assignment:
            rendered.append(assignment.group(1) + "=<redacted>")
            continue
        rendered.append(_SECRET_DSN.sub(r"\1<redacted>", value))
    return rendered


def render_command(argv: list[str]) -> str:
    return shlex.join(safe_argv(argv))


_SH_WRAPPERS = {"sh", "bash", "dash", "ksh", "zsh"}
# Wrappers whose ``-c`` flag takes the real command as the next argument
# (``script -qefc 'cmd' /dev/null`` records a pseudo-tty transcript).
_C_FLAG_WRAPPERS = _SH_WRAPPERS | {"script"}
# psql transport/formatting flags dropped from the display form — they
# shape output bytes, not what the statement means; full argv stays in
# the command evidence JSON.
_PSQL_FORMAT_FLAGS = {
    "-X", "-A", "-t", "-q", "-a", "-e", "-E", "-n", "-x",
    "--csv", "--expanded", "--no-align", "--no-psqlrc", "--no-readline",
    "--tuples-only", "--quiet", "--all", "--echo-all", "--echo-errors",
}
_PSQL_FORMAT_FLAGS_ARG = {
    "-v", "-P", "-o", "-L", "--set", "--pset", "--variable",
    "--output", "--log-file",
}
_PSQL_FORMAT_PREFIX = ("-v", "-P", "--set=", "--pset=", "--variable=",
                       "--output=", "--log-file=")
# Letters that may appear in combined short flags like ``-Atx``.
_PSQL_FORMAT_LETTERS = frozenset("XAtqaeEnx")


def _psql_display(safe: list[str]) -> list[str]:
    """Drop formatting flags, keep connection/semantic flags and the SQL."""
    kept = [safe[0]]
    index = 1
    while index < len(safe):
        arg = safe[index]
        if arg in _PSQL_FORMAT_FLAGS:
            index += 1
        elif arg in _PSQL_FORMAT_FLAGS_ARG:
            index += 2
        elif arg.startswith(_PSQL_FORMAT_PREFIX) or (
                arg.startswith("-") and not arg.startswith("--")
                and set(arg[1:]) <= _PSQL_FORMAT_LETTERS):
            index += 1
        else:
            kept.append(arg)
            index += 1
    return kept


def _shell_lines(script: str) -> list[str]:
    """Split a shell one-liner at unquoted ``;``/``&&``/``||`` boundaries."""
    lines: list[str] = []
    buf: list[str] = []
    depth = 0
    quote = ""
    index = 0
    while index < len(script):
        ch = script[index]
        if quote:
            buf.append(ch)
            if ch == quote and script[index - 1] != "\\":
                quote = ""
            index += 1
            continue
        if ch in "'\"`":
            quote = ch
        elif script.startswith("$(", index):
            depth += 1
            buf.append("$(")
            index += 2
            continue
        elif ch == "(":
            depth += 1
        elif ch == ")" and depth:
            depth -= 1
        elif depth == 0 and ch == ";":
            buf.append(";")
            lines.append("".join(buf).strip())
            buf = []
            index += 1
            continue
        buf.append(ch)
        index += 1
    tail = "".join(buf).strip()
    if tail:
        lines.append(tail)
    return lines


def _indent_shell(lines: list[str]) -> str:
    indent = 0
    rendered = []
    for line in lines:
        words = line.split()
        first = words[0].rstrip(";") if words else ""
        if first in ("done", "fi", "esac", "}", "elif", "else"):
            indent = max(0, indent - 1)
        rendered.append("  " * indent + line)
        if first in ("do", "then", "else", "elif", "case"):
            indent += 1
    return "\n".join(rendered)


def _format_shell(script: str) -> str:
    """Reformat a long one-line shell script for review readability."""
    if "\n" in script or len(script) < 100:
        return script
    lines = _shell_lines(script)
    if len(lines) < 3:
        return script
    return _indent_shell(lines)


def display_command(argv: list[str], override: str | None = None) -> str:
    """Human-readable command for report ``执行内容``.

    ``sh -c``/``sh -ec`` wrappers show the inner script verbatim and psql
    ``-c`` shows the SQL unquoted — the escaped one-liner form is only
    useful for exact replay and stays in the command evidence JSON.
    """
    if override:
        return _SECRET_DSN.sub(r"\1<redacted>", str(override))
    safe = safe_argv([str(value) for value in argv])
    prefix = ""
    if safe and safe[0] == "env":
        consumed = ["env"]
        safe = safe[1:]
        while safe and "=" in safe[0] and not safe[0].startswith("-"):
            consumed.append(safe.pop(0))
        prefix = " ".join(shlex.quote(arg) for arg in consumed) + " "

    def finish(text: str) -> str:
        return prefix + _SECRET_DSN.sub(r"\1<redacted>", text)

    if len(safe) >= 3 and Path(safe[0]).name in _C_FLAG_WRAPPERS:
        for index in range(1, len(safe) - 1):
            flags = safe[index]
            if flags.startswith("-") and "c" in flags.lstrip("-"):
                return finish(_format_shell(safe[index + 1]))
    if len(safe) >= 3 and Path(safe[0]).name == "psql":
        kept = _psql_display(safe)
        for flag in ("-c", "--command"):
            if flag in kept:
                index = kept.index(flag)
                if index + 1 < len(kept):
                    head = " ".join(
                        shlex.quote(arg) for arg in kept[:index + 1])
                    tail = kept[index + 1]
                    extra = (" " + shlex.join(kept[index + 2:])
                             ) if index + 2 < len(kept) else ""
                    return finish(f"{head}\n  {tail}{extra}")
        return finish(" ".join(shlex.quote(arg) for arg in kept))
    if len(safe) >= 3 and Path(safe[0]).name == "ssh":
        return finish(f"ssh {safe[-2]} {_format_shell(safe[-1])}")
    return prefix + shlex.join(safe)


class CommandExecutor:
    """Own commands operations for one case execution."""

    def __init__(self, context: CaseContext):
        self.context = context
        self._processes = []
        self._process_handles = {}
        self._process_logs = []
        self._background_inputs = {}

    def command(
        self,
        argv: list[str],
        *,
        cwd: Path | None = None,
        timeout_seconds: float | None = 30,
        input_text: str | None = None,
        merge_stderr: bool = False,
    ) -> CommandResult:
        """Run a product command without a shell and retain its output as evidence.

        A nonzero exit is returned to the case, which owns the business
        assertion. Timeout and cancellation interrupt the whole process group.
        ``timeout_seconds=None`` waits without a deadline (cancellation still
        applies). ``input_text`` is written to the process stdin. With
        ``merge_stderr`` stderr folds into stdout like ``stderr=STDOUT``;
        on timeout the raised ``TimeoutError`` carries ``partial_stdout`` and
        ``partial_stderr`` so callers can keep legacy rc=124 semantics.
        """
        if not argv or any(not isinstance(arg, str) for arg in argv):
            raise ValueError("命令必须是非空字符串参数数组")
        if timeout_seconds is not None and timeout_seconds <= 0:
            raise ValueError("命令超时必须大于零")
        self.context.check_cancel()
        key = self.context.next_evidence_key("command")
        self.context.emit(
            "command.started", {"step_key": key, "executable": Path(argv[0]).name}
        )
        started = time.monotonic()
        try:
            process = subprocess.Popen(
                argv,
                cwd=cwd,
                stdin=subprocess.PIPE if input_text is not None else None,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT if merge_stderr else subprocess.PIPE,
                text=True,
                start_new_session=True,
            )
        except OSError as exc:
            self.context.emit("command.failed", {"step_key": key, "reason": str(exc)})
            raise
        pending_input = input_text
        try:
            while True:
                self.context.check_cancel()
                if timeout_seconds is None:
                    wait_slice = 0.2
                else:
                    remaining = timeout_seconds - (time.monotonic() - started)
                    if remaining <= 0:
                        raise TimeoutError(f"命令超过 {timeout_seconds:g} 秒")
                    wait_slice = min(remaining, 0.2)
                try:
                    stdout, stderr = process.communicate(
                        input=pending_input, timeout=wait_slice
                    )
                    break
                except subprocess.TimeoutExpired:
                    pending_input = None
                    continue
        except (Cancelled, TimeoutError) as exc:
            # A case can launch child processes; stop the process group so a
            # timed-out test cannot keep modifying its environment afterward.
            # TERM first, then KILL after a grace — the legacy runner's
            # two-stage termination lets postmasters release their ports.
            self._terminate_process_group(process)
            stdout, stderr = self._drain_stopped(process)
            if merge_stderr:
                stderr = ""
            reference = self.context.attach_text(
                key + ".json",
                json.dumps(
                    {
                        "command": render_command(argv),
                        "argv": safe_argv(argv),
                        "cwd": str(cwd) if cwd is not None else None,
                        "stdout": stdout,
                        "stderr": stderr,
                        "error": str(exc),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
            )
            self.context.emit(
                "command.failed",
                {"step_key": key, "reason": str(exc), "evidence": reference},
            )
            if isinstance(exc, TimeoutError):
                exc.partial_stdout = stdout
                exc.partial_stderr = stderr
            raise
        duration = time.monotonic() - started
        reference = self.context.attach_text(
            key + ".json",
            json.dumps(
                {
                    "command": render_command(argv),
                    "argv": safe_argv(argv),
                    "cwd": str(cwd) if cwd is not None else None,
                    "returncode": process.returncode,
                    "stdout": stdout or "",
                    "stderr": stderr or "",
                    "duration_seconds": duration,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
        )
        result = CommandResult(
            process.returncode, stdout or "", stderr or "", duration, reference
        )
        self.context.emit(
            "command.finished",
            {
                "step_key": key,
                "returncode": result.returncode,
                "evidence": reference,
            },
        )
        self.context.check_cancel()
        return result

    def start_command(
        self, argv: list[str], input_text: str | None = None
    ) -> subprocess.Popen:
        """Spawn argv as a background process and return its handle.

        Mirrors the legacy runner's ``start``: stderr folds into stdout and
        ``input_text`` is fed into stdin which is then closed. Pair with
        :meth:`finish_command` which preserves the legacy timeout semantics
        (returncode 124 plus a ``，已终止`` marker on partial output).
        """
        if not argv or any(not isinstance(arg, str) for arg in argv):
            raise ValueError("命令必须是非空字符串参数数组")
        self.context.check_cancel()
        process = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )
        if input_text is not None:
            future = Future()
            self._background_inputs[id(process)] = future

            def communicate():
                try:
                    future.set_result(process.communicate(input=input_text))
                except BaseException as exc:
                    future.set_exception(exc)

            threading.Thread(
                target=communicate, name="command-input", daemon=True
            ).start()
        self.context.defer_cleanup(lambda: self._cleanup_background(process))
        return process

    def finish_command(
        self,
        argv: list[str],
        process: subprocess.Popen,
        timeout_seconds: float | None = None,
        input_text: str | None = None,
    ) -> subprocess.CompletedProcess:
        """Wait for a process spawned by :meth:`start_command`.

        On timeout the process group is terminated (TERM, then KILL after a
        grace period) and a CompletedProcess with ``returncode == 124`` and
        the legacy ``命令执行超时（…），已终止`` marker is returned, matching
        ``command_runner.finish`` exactly.
        """
        future = self._background_inputs.get(id(process))
        pending_input = input_text
        started = time.monotonic()
        timed_out = False
        try:
            while True:
                self.context.check_cancel()
                if timeout_seconds is None:
                    wait_slice = 0.2
                else:
                    remaining = timeout_seconds - (time.monotonic() - started)
                    if remaining <= 0:
                        timed_out = True
                        break
                    wait_slice = min(remaining, 0.2)
                try:
                    if future is not None:
                        stdout, _ = future.result(timeout=wait_slice)
                    else:
                        stdout, _ = process.communicate(
                            input=pending_input, timeout=wait_slice
                        )
                    break
                except (subprocess.TimeoutExpired, FutureTimeout):
                    pending_input = None
                    continue
        except Cancelled:
            self._terminate_process_group(process)
            self._drain_stopped(process, future)
            raise
        if timed_out:
            self._terminate_process_group(process)
            stdout, _ = self._drain_stopped(process, future)
            output = (stdout or "") + f"\n命令执行超时（{timeout_seconds}s），已终止"
            return subprocess.CompletedProcess(list(argv), 124, output, None)
        return subprocess.CompletedProcess(
            list(argv), process.returncode, stdout or "", None
        )

    def tcp_exchange(
        self,
        host: str,
        port: int,
        payload: bytes = b"",
        *,
        timeout_seconds: float = 5,
        expected_bytes: int | None = None,
        until_eof: bool = False,
    ) -> bytes:
        """Exchange bytes with a product endpoint and retain the response."""
        if not host or not 1 <= port <= 65535:
            raise ValueError("协议端点无效")
        self.context.check_cancel()
        started = time.monotonic()
        try:
            with socket.create_connection(
                (host, port), timeout=timeout_seconds
            ) as connection:
                connection.settimeout(timeout_seconds)
                if payload:
                    connection.sendall(payload)
                chunks = []
                received = 0
                while not until_eof and (
                    expected_bytes is None or received < expected_bytes
                ):
                    chunk = connection.recv(65536)
                    if not chunk:
                        break
                    chunks.append(chunk)
                    received += len(chunk)
                    if expected_bytes is None:
                        break
                if until_eof:
                    while True:
                        chunk = connection.recv(65536)
                        if not chunk:
                            break
                        chunks.append(chunk)
                data = b"".join(chunks)
        except OSError as exc:
            self.context.emit(
                "protocol.failed", {"host": host, "port": port, "reason": str(exc)}
            )
            raise
        reference = self.context.attach_text(
            self.context.next_evidence_key("protocol") + ".json",
            json.dumps(
                {
                    "host": host,
                    "port": port,
                    "sent_bytes": len(payload),
                    "received_bytes": len(data),
                    "duration_seconds": time.monotonic() - started,
                    "response_hex": data.hex(),
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
        )
        self.context.emit(
            "protocol.finished",
            {
                "host": host,
                "port": port,
                "received_bytes": len(data),
                "evidence": reference,
            },
        )
        return data

    def start_process(
        self,
        argv: list[str],
        *,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        ready_host: str | None = None,
        ready_port: int | None = None,
        timeout_seconds: float = 30,
    ) -> subprocess.Popen:
        """Start a product-owned executable and stop it during SDK cleanup."""
        if not argv or any(not item for item in argv):
            raise ValueError("进程参数无效")
        self.context.check_cancel()
        log_path = self.context.output_dir / (
            "process-%d.log" % (len(self._processes) + 1)
        )
        self._process_logs.append(log_path)
        log_handle = log_path.open("a", encoding="utf-8")
        try:
            process = subprocess.Popen(
                argv,
                cwd=cwd,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                text=True,
            )
        except Exception:
            log_handle.close()
            raise
        self._processes.append(process)
        self._process_handles[id(process)] = log_handle
        self.context.defer_cleanup(
            lambda: self._stop_process(
                process, self._process_handles.pop(id(process), None)
            )
        )
        self.context.emit(
            "process.started", {"executable": Path(argv[0]).name, "pid": process.pid}
        )
        deadline = time.monotonic() + timeout_seconds
        if ready_host and ready_port:
            while time.monotonic() < deadline:
                self.context.check_cancel()
                if process.poll() is not None:
                    raise RuntimeError(f"产品进程提前退出: rc={process.returncode}")
                try:
                    with socket.create_connection(
                        (ready_host, ready_port), timeout=0.2
                    ):
                        self.context.emit(
                            "process.ready",
                            {
                                "pid": process.pid,
                                "host": ready_host,
                                "port": ready_port,
                            },
                        )
                        return process
                except OSError:
                    time.sleep(0.1)
            raise TimeoutError(f"产品进程未在 {timeout_seconds:g} 秒内就绪")
        return process

    @staticmethod
    def _terminate_process_group(process: subprocess.Popen) -> None:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=2)

    @staticmethod
    def _drain_stopped(process, future=None):
        try:
            return (
                future.result(timeout=2)
                if future is not None
                else process.communicate(timeout=2)
            )
        except (subprocess.TimeoutExpired, FutureTimeout):
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            return (
                future.result(timeout=2)
                if future is not None
                else process.communicate(timeout=2)
            )

    def _cleanup_background(self, process):
        future = self._background_inputs.pop(id(process), None)
        try:
            if process.poll() is None or (future is not None and not future.done()):
                self._terminate_process_group(process)
                self._drain_stopped(process, future)
        finally:
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream is not None:
                    stream.close()

    @staticmethod
    def _stop_process(process: subprocess.Popen, log_handle) -> None:
        """Terminate the complete process group and close its log safely."""
        try:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=5)
        finally:
            if log_handle is not None:
                log_handle.close()

    def stop_processes(self) -> None:
        for process in reversed(self._processes):
            handle = self._process_handles.pop(id(process), None)
            self._stop_process(process, handle)
        self._processes.clear()

    def archive_logs(self) -> list[str]:
        errors = []
        for index, path in enumerate(self._process_logs, 1):
            if path.is_file():
                try:
                    self.context.attach_file(f"process-{index}.log", path)
                except Exception as exc:
                    errors.append(f"无法归档产品进程日志: {exc}")
        return errors
