from __future__ import annotations

import json
import os
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
        result = CommandResult(
            process.returncode, stdout or "", stderr or "", time.monotonic() - started
        )
        reference = self.context.attach_text(
            key + ".json",
            json.dumps(
                {
                    "returncode": result.returncode,
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                    "duration_seconds": result.duration_seconds,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
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
