"""Product-neutral regression case lifecycle and evidence recording."""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import socket
import subprocess
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Protocol

import psycopg


class Blocked(RuntimeError):
    """A required environment condition prevented business verification."""


class Cancelled(RuntimeError):
    """The execution received a cooperative cancellation request."""


class CaseFailure(Exception):
    """A business check failed; maps to the FAIL verdict.

    Product runtime adapters raise domain failures derived from this base so
    the engine can distinguish a genuine test failure from an executor or
    infrastructure defect (which remains ERROR).
    """


class RegressionCase(Protocol):
    def run(self, context: "CaseContext") -> bool | None: ...


@dataclass(frozen=True)
class CleanupResult:
    status: str
    reason: str | None = None


@dataclass(frozen=True)
class SqlResult:
    rows: tuple[tuple[str | None, ...], ...]
    columns: tuple[str, ...]
    command_tag: str


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str
    duration_seconds: float


@dataclass(frozen=True)
class CaseResult:
    schema_version: str
    execution_id: str
    operation_id: str | None
    target: str
    verdict: str
    business_verdict: str
    reason: str | None
    cleanup: CleanupResult
    duration_seconds: float
    evidence: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["evidence"] = list(self.evidence)
        return result


def resolve_selector(environment: dict[str, Any],
                     selector: str | None = "primary") -> str:
    """Resolve a product node selector to an injected endpoint key.

    Mirrors the legacy topology contract exactly: a direct node key wins;
    ``primary``/``writable`` prefer the streaming primary, then the first MMR
    member's primary in topology order, then the logical publisher, then the
    first declared node; ``standby`` resolves only from the streaming group;
    ``subscriber``/``logical_subscriber`` take the first logical subscriber;
    ``mmr:<member>[:primary|standby]`` resolves through the MMR member map.
    Products describe their groups via ``environment["node_groups"]``.
    """
    nodes = environment.get("nodes") or {}
    selector = str(selector or "primary")
    if selector in nodes:
        return selector
    groups = environment.get("node_groups") or {}
    streaming = groups.get("streaming") or {}
    logical = groups.get("logical") or {}
    members = (groups.get("mmr") or {}).get("members") or {}
    if selector in ("primary", "writable"):
        if streaming.get("primary") and streaming["primary"] in nodes:
            return streaming["primary"]
        if members:
            primary = next(iter(members.values()))["primary"]
            if primary in nodes:
                return primary
        if logical.get("publisher") and logical["publisher"] in nodes:
            return logical["publisher"]
        if nodes:
            return next(iter(nodes))
        raise Blocked("无法解析测试节点: %s" % selector)
    if selector == "standby" and streaming.get("standbys"):
        candidate = streaming["standbys"][0]
        if candidate in nodes:
            return candidate
    if selector == "publisher" and logical.get("publisher") in nodes:
        return logical["publisher"]
    if selector in ("subscriber", "logical_subscriber"):
        subscribers = logical.get("subscribers") or {}
        for candidate in subscribers:
            if candidate in nodes:
                return candidate
    parts = selector.split(":")
    if selector.startswith("mmr:"):
        relation = members.get(parts[1]) if len(parts) > 1 else None
        if relation:
            if len(parts) == 2 or parts[2] == "primary":
                candidate = relation["primary"]
                if candidate in nodes:
                    return candidate
            elif parts[2] == "standby" and relation.get("standbys"):
                candidate = relation["standbys"][0]
                if candidate in nodes:
                    return candidate
    # Compatibility: ``member:role`` resolves to an explicit ``member_role``
    # endpoint key when products inject flat names instead of groups.
    if len(parts) >= 2:
        key = parts[-2] if parts[-1] == "primary" else "%s_%s" % (parts[-2], parts[-1])
        if key in nodes:
            return key
    # Product convenience aliases (e.g. bare MMR member names) resolve last so
    # they can never shadow a real node key or a group-based selector.
    aliases = environment.get("node_aliases") or {}
    if aliases.get(selector) in nodes:
        return aliases[selector]
    raise Blocked("无法解析测试节点: %s" % selector)


class CaseContext:
    """Controlled case output and cancellation surface shared by products."""

    def __init__(self, target: str, output_dir: Path,
                 cancelled: Callable[[], bool] | None = None,
                 environment: dict[str, Any] | None = None,
                 operation_id: str | None = None,
                 run_id: str | None = None):
        self.target = target
        self.output_dir = output_dir
        self.execution_id = uuid.uuid4().hex
        self.operation_id = operation_id
        self._cancelled = cancelled or (lambda: False)
        self.environment = environment or {}
        # One invocation may share a run_id across case contexts so session
        # fixtures and member cases expand identical {run_id} placeholders.
        self.run_id = run_id or f"run_{datetime.now():%Y%m%d_%H%M%S}_{self.execution_id[:6]}"
        self.values: dict[str, Any] = {"run_id": self.run_id}
        self._sequence = 0
        self._sql_sequence = 0
        self._evidence: list[str] = []
        self._cleanup_actions: list[tuple[int, int, Callable[[], None]]] = []
        self._cleanup_sequence = 0
        self._processes: list[subprocess.Popen] = []
        self._process_handles: dict[int, Any] = {}
        self._process_logs: list[Path] = []
        output_dir.mkdir(parents=True, exist_ok=True)
        # The directory contains only the current result. A rerun starts a new
        # event sequence; previous attachments remain unreferenced until pruned.
        (output_dir / "events.jsonl").write_text("", encoding="utf-8")

    @property
    def evidence(self) -> tuple[str, ...]:
        return tuple(self._evidence)

    def check_cancel(self) -> None:
        if self._cancelled():
            raise Cancelled("用例已取消")

    def defer_cleanup(self, action: Callable[[], None], *, priority: int = 0) -> None:
        """Register an idempotent product fixture cleanup action.

        Cleanup drains highest priority first; actions with the same priority
        run in reverse registration order. Products use priorities to order
        resource teardown (e.g. stop postmasters before releasing ports).
        """
        self._cleanup_actions.append((priority, self._cleanup_sequence, action))
        self._cleanup_sequence += 1

    def stop_processes(self) -> None:
        """Stop product processes started by this case before starting a replacement."""
        for process in reversed(self._processes):
            handle = self._process_handles.pop(id(process), None)
            self._stop_process(process, handle)
        self._processes.clear()

    def cleanup_fixtures(self) -> None:
        """Run registered fixture cleanup in reverse order."""
        errors = []
        pending = sorted(self._cleanup_actions, key=lambda item: item[:2])
        self._cleanup_actions.clear()
        while pending:
            _, _, action = pending.pop()
            try:
                action()
            except Exception as exc:
                errors.append(str(exc))
        for index, path in enumerate(self._process_logs, 1):
            if path.is_file():
                try:
                    self.attach_file(f"process-{index}.log", path)
                except Exception as exc:
                    errors.append(f"无法归档产品进程日志: {exc}")
        if errors:
            raise RuntimeError("; ".join(errors))

    def expand(self, value: Any) -> Any:
        """Expand per-run placeholders in declarative fixtures and steps.

        ``{run_id}`` becomes this execution's unique id; strings holding a
        declared listener port are rewritten to the reserved port recorded in
        ``values["isolated_mmr_port_mapping"]`` (``port=<n>``, ``-p <n>`` and ``:<n>``
        forms), so product cases can keep their exported commands verbatim.
        """
        if isinstance(value, str):
            expanded = value.replace("{run_id}", str(self.values["run_id"]))
            ports = self.values.get("isolated_mmr_port_mapping") or {}
            if expanded in ports:
                return ports[expanded]
            for declared, allocated in ports.items():
                escaped = re.escape(str(declared))
                expanded = re.sub(r"(port\s*=\s*)%s\b" % escaped,
                                  r"\g<1>%s" % allocated, expanded)
                expanded = re.sub(r"(\B-p\s+)%s\b" % escaped,
                                  r"\g<1>%s" % allocated, expanded)
                expanded = re.sub(r"(:)%s\b" % escaped,
                                  r"\g<1>%s" % allocated, expanded)
            return expanded
        if isinstance(value, list):
            return [self.expand(item) for item in value]
        if isinstance(value, tuple):
            return tuple(self.expand(item) for item in value)
        if isinstance(value, dict):
            return {key: self.expand(item) for key, item in value.items()}
        return value

    def resolve_node(self, selector: str = "primary") -> str:
        """Map a product node selector to an injected endpoint key."""
        return resolve_selector(self.environment, selector)

    def node_endpoint(self, selector: str = "primary") -> dict[str, Any]:
        """Return the injected ``{host, port}`` endpoint for a selector."""
        key = self.resolve_node(selector)
        endpoint = (self.environment.get("nodes") or {}).get(key)
        if not isinstance(endpoint, dict):
            raise Blocked(f"测试节点连接信息无效: {key}")
        return endpoint

    @staticmethod
    def is_local(host: Any) -> bool:
        host = str(host)
        if host in {"127.0.0.1", "localhost", "local", "::1"}:
            return True
        try:
            local_addresses = {item[4][0] for item in socket.getaddrinfo(socket.gethostname(), None)}
            target_addresses = {item[4][0] for item in socket.getaddrinfo(host, None)}
            return bool(local_addresses & target_addresses)
        except socket.gaierror:
            return False

    def reload(self, node: str, *, user: str | None = None) -> None:
        """Reload PostgreSQL configuration through the declared node."""
        kwargs = {"user": user} if user else {}
        self.sql(node, "SELECT pg_reload_conf()", **kwargs)
        self.step("fixture-reload", "重载数据库配置")

    def set_setting(self, node: str, name: str, value: str, *, user: str | None = None) -> None:
        """Set a runtime setting and restore its previous value afterwards."""
        if not name.replace("_", "").replace(".", "").isalnum():
            raise ValueError("配置参数名无效")
        kwargs = {"user": user} if user else {}
        old = self.sql(node, f"SELECT current_setting('{name}', true)", **kwargs).rows
        old_value = old[0][0] if old and old[0] else None
        escaped = value.replace("'", "''")
        self.sql(node, f"ALTER SYSTEM SET {name} = '{escaped}'", **kwargs)
        self.reload(node, user=user)
        restore = "RESET" if old_value is None else f"SET {name} = '{old_value.replace(chr(39), chr(39) * 2)}'"
        self.defer_cleanup(lambda: (self.sql(node, f"ALTER SYSTEM {restore}", **kwargs),
                                    self.reload(node, user=user)))

    def create_role(self, node: str, name: str, attributes: str = "") -> None:
        """Create a temporary role and guarantee cleanup after the case."""
        if not name.replace("_", "").isalnum():
            raise ValueError("角色名无效")
        suffix = (" " + attributes.strip()) if attributes.strip() else ""
        self.sql(node, f"CREATE ROLE {name}{suffix}")
        self.defer_cleanup(lambda: self.sql(node, f"DROP ROLE IF EXISTS {name}"))

    def defer_drop_table(self, node: str, name: str) -> None:
        """Register cleanup for a table declared by a catalog fixture."""
        if not name.replace("_", "").isalnum():
            raise ValueError("表名无效")
        self.defer_cleanup(lambda: self.sql(node, f"DROP TABLE IF EXISTS {name}"))

    def emit(self, kind: str, payload: dict[str, Any]) -> None:
        """Append one ordered fact before clients can observe the event."""
        self._sequence += 1
        event = {
            "schema_version": "1.0", "execution_id": self.execution_id,
            "operation_id": self.operation_id,
            "sequence": self._sequence,
            "recorded_at": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "target": self.target, "kind": kind, "payload": payload,
        }
        with (self.output_dir / "events.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def step(self, key: str, title: str, *, status: str = "PASS",
             details: dict[str, Any] | None = None) -> None:
        if not key or not title:
            raise ValueError("步骤标识和标题不能为空")
        self.emit("step.finished", {
            "step_key": key, "title": title, "status": status,
            "details": details or {},
        })

    def attach_text(self, name: str, content: str) -> str:
        """Write a named evidence file without allowing path traversal."""
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", name):
            raise ValueError("证据文件名无效")
        directory = self.output_dir / "artifacts" / self.execution_id
        directory.mkdir(parents=True, exist_ok=True)
        destination = directory / name
        temporary = directory / (name + ".tmp")
        temporary.write_text(content, encoding="utf-8")
        os.replace(temporary, destination)
        reference = "artifacts/" + self.execution_id + "/" + name
        if reference not in self._evidence:
            self._evidence.append(reference)
        self.emit("evidence.attached", {"ref": reference})
        return reference

    def attach_bytes(self, name: str, content: bytes) -> str:
        """Write binary evidence (e.g. collected server logs) under a safe name."""
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", name):
            raise ValueError("证据文件名无效")
        directory = self.output_dir / "artifacts" / self.execution_id
        directory.mkdir(parents=True, exist_ok=True)
        destination = directory / name
        temporary = directory / (name + ".tmp")
        with temporary.open("wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
        reference = "artifacts/" + self.execution_id + "/" + name
        if reference not in self._evidence:
            self._evidence.append(reference)
        self.emit("evidence.attached", {"ref": reference})
        return reference

    def attach_file(self, name: str, source: Path) -> str:
        """Keep a run's source artifact after its product package is removed."""
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", name):
            raise ValueError("证据文件名无效")
        directory = self.output_dir / "artifacts" / self.execution_id
        directory.mkdir(parents=True, exist_ok=True)
        destination = directory / name
        temporary = directory / (name + ".tmp")
        with source.open("rb") as incoming, temporary.open("wb") as outgoing:
            shutil.copyfileobj(incoming, outgoing)
            outgoing.flush()
            os.fsync(outgoing.fileno())
        os.replace(temporary, destination)
        reference = "artifacts/" + self.execution_id + "/" + name
        if reference not in self._evidence:
            self._evidence.append(reference)
        self.emit("evidence.attached", {"ref": reference})
        return reference

    def sql(self, node: str, query: str, *, database: str = "postgres",
            user: str | None = None, password: str | None = None) -> SqlResult:
        """Execute one SQL statement against a declared product node.

        SQL text and returned rows become evidence. Product cases own the
        expected values and assertions; connection setup stays in this SDK.
        """
        self.check_cancel()
        nodes = self.environment.get("nodes") or {}
        endpoint = nodes.get(node)
        if not isinstance(endpoint, dict):
            # Native cases address nodes by product aliases; resolve them to
            # the canonical endpoint key like the single-target context did.
            endpoint = nodes.get(self.resolve_node(node))
        if not isinstance(endpoint, dict):
            raise Blocked(f"未配置测试节点: {node}")
        host, port = endpoint.get("host"), endpoint.get("port")
        if not isinstance(host, str) or not isinstance(port, int):
            raise Blocked(f"测试节点连接信息无效: {node}")
        self._sql_sequence += 1
        key = f"sql-{self._sql_sequence}"
        users = self.environment.get("users") or {}
        selected_user = user or self.environment.get("user") or "postgres"
        selected_password = password
        if selected_password is None:
            selected_password = (users.get(selected_user, {}).get("password")
                                 if isinstance(users, dict) else None)
        self.emit("sql.started", {"step_key": key, "node": node, "database": database,
                                   "user": selected_user})
        try:
            with psycopg.connect(
                host=host, port=port, dbname=database,
                user=selected_user, password=selected_password,
                connect_timeout=5, options="-c statement_timeout=10000",
                autocommit=True,
            ) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(query)
                    rows = cursor.fetchall() if cursor.description else []
                    columns = tuple(item.name for item in cursor.description) if cursor.description else ()
                    tag = cursor.statusmessage or ""
        except Exception as exc:
            reference = self.attach_text(key + ".json", json.dumps({
                "node": node, "database": database, "sql": query,
                "sqlstate": getattr(exc, "sqlstate", None), "error": str(exc),
            }, ensure_ascii=False, indent=2) + "\n")
            self.emit("sql.failed", {
                "step_key": key, "node": node, "reason": str(exc), "evidence": reference,
            })
            raise
        normalized = tuple(tuple(None if value is None else str(value) for value in row) for row in rows)
        result = SqlResult(normalized, columns, tag)
        reference = self.attach_text(
            key + ".json",
            json.dumps({"node": node, "database": database, "sql": query,
                        "columns": columns, "rows": normalized, "command_tag": tag},
                       ensure_ascii=False, indent=2) + "\n",
        )
        self.emit("sql.finished", {"step_key": key, "node": node, "rows": len(rows), "evidence": reference})
        self.check_cancel()
        return result

    def command(self, argv: list[str], *, cwd: Path | None = None,
                timeout_seconds: float | None = 30, input_text: str | None = None,
                merge_stderr: bool = False) -> CommandResult:
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
        self.check_cancel()
        self._sql_sequence += 1
        key = f"command-{self._sql_sequence}"
        self.emit("command.started", {"step_key": key, "executable": Path(argv[0]).name})
        started = time.monotonic()
        try:
            process = subprocess.Popen(
                argv, cwd=cwd,
                stdin=subprocess.PIPE if input_text is not None else None,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT if merge_stderr else subprocess.PIPE,
                text=True, start_new_session=True,
            )
        except OSError as exc:
            self.emit("command.failed", {"step_key": key, "reason": str(exc)})
            raise
        if input_text is not None:
            try:
                process.stdin.write(input_text)
                process.stdin.close()
            except OSError:
                # The child exited before consuming stdin; its exit status
                # remains the authoritative fact for the case's assertion.
                pass
            finally:
                process.stdin = None
        try:
            while True:
                self.check_cancel()
                if timeout_seconds is None:
                    wait_slice = 0.2
                else:
                    remaining = timeout_seconds - (time.monotonic() - started)
                    if remaining <= 0:
                        raise TimeoutError(f"命令超过 {timeout_seconds:g} 秒")
                    wait_slice = min(remaining, 0.2)
                try:
                    stdout, stderr = process.communicate(timeout=wait_slice)
                    break
                except subprocess.TimeoutExpired:
                    continue
        except (Cancelled, TimeoutError) as exc:
            # A case can launch child processes; stop the process group so a
            # timed-out test cannot keep modifying its environment afterward.
            # TERM first, then KILL after a grace — the legacy runner's
            # two-stage termination lets postmasters release their ports.
            self._terminate_process_group(process)
            stdout, stderr = process.communicate()
            if merge_stderr:
                stderr = ""
            reference = self.attach_text(key + ".json", json.dumps({
                "stdout": stdout, "stderr": stderr, "error": str(exc),
            }, ensure_ascii=False, indent=2) + "\n")
            self.emit("command.failed", {"step_key": key, "reason": str(exc), "evidence": reference})
            if isinstance(exc, TimeoutError):
                exc.partial_stdout = stdout
                exc.partial_stderr = stderr
            raise
        result = CommandResult(process.returncode, stdout or "", stderr or "",
                               time.monotonic() - started)
        reference = self.attach_text(key + ".json", json.dumps({
            "returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr,
            "duration_seconds": result.duration_seconds,
        }, ensure_ascii=False, indent=2) + "\n")
        self.emit("command.finished", {
            "step_key": key, "returncode": result.returncode, "evidence": reference,
        })
        self.check_cancel()
        return result

    def start_command(self, argv: list[str],
                      input_text: str | None = None) -> subprocess.Popen:
        """Spawn argv as a background process and return its handle.

        Mirrors the legacy runner's ``start``: stderr folds into stdout and
        ``input_text`` is fed into stdin which is then closed. Pair with
        :meth:`finish_command` which preserves the legacy timeout semantics
        (returncode 124 plus a ``，已终止`` marker on partial output).
        """
        if not argv or any(not isinstance(arg, str) for arg in argv):
            raise ValueError("命令必须是非空字符串参数数组")
        self.check_cancel()
        process = subprocess.Popen(
            argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, start_new_session=True,
        )
        if input_text is not None:
            try:
                process.stdin.write(input_text)
                process.stdin.close()
            except OSError:
                # The child exited before consuming stdin; its exit status
                # remains the authoritative fact for the caller's assertion.
                pass
            finally:
                process.stdin = None
        return process

    def finish_command(self, argv: list[str], process: subprocess.Popen,
                       timeout_seconds: float | None = None,
                       input_text: str | None = None) -> subprocess.CompletedProcess:
        """Wait for a process spawned by :meth:`start_command`.

        On timeout the process group is terminated (TERM, then KILL after a
        grace period) and a CompletedProcess with ``returncode == 124`` and
        the legacy ``命令执行超时（…），已终止`` marker is returned, matching
        ``command_runner.finish`` exactly.
        """
        started = time.monotonic()
        timed_out = False
        try:
            while True:
                self.check_cancel()
                if timeout_seconds is None:
                    wait_slice = 0.2
                else:
                    remaining = timeout_seconds - (time.monotonic() - started)
                    if remaining <= 0:
                        timed_out = True
                        break
                    wait_slice = min(remaining, 0.2)
                try:
                    stdout, _ = process.communicate(timeout=wait_slice)
                    break
                except subprocess.TimeoutExpired:
                    continue
        except Cancelled:
            self._terminate_process_group(process)
            process.communicate()
            raise
        if timed_out:
            self._terminate_process_group(process)
            stdout, _ = process.communicate()
            output = (stdout or "") + "\n命令执行超时（%ss），已终止" % timeout_seconds
            return subprocess.CompletedProcess(list(argv), 124, output, None)
        return subprocess.CompletedProcess(
            list(argv), process.returncode, stdout or "", None)

    @staticmethod
    def _terminate_process_group(process: subprocess.Popen) -> None:
        """SIGTERM the process group, escalating to SIGKILL after a grace."""
        if process.poll() is not None:
            return
        try:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=2)
        except (OSError, subprocess.TimeoutExpired):
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except OSError:
                pass

    def tcp_exchange(self, host: str, port: int, payload: bytes = b"",
                     *, timeout_seconds: float = 5,
                     expected_bytes: int | None = None,
                     until_eof: bool = False) -> bytes:
        """Exchange bytes with a product endpoint and retain the response."""
        if not host or not 1 <= port <= 65535:
            raise ValueError("协议端点无效")
        self.check_cancel()
        started = time.monotonic()
        try:
            with socket.create_connection((host, port), timeout=timeout_seconds) as connection:
                connection.settimeout(timeout_seconds)
                if payload:
                    connection.sendall(payload)
                chunks = []
                received = 0
                while not until_eof and (expected_bytes is None or received < expected_bytes):
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
            self.emit("protocol.failed", {"host": host, "port": port, "reason": str(exc)})
            raise
        reference = self.attach_text(
            f"protocol-{self._sql_sequence + 1}.json",
            json.dumps({"host": host, "port": port, "sent_bytes": len(payload),
                        "received_bytes": len(data), "duration_seconds": time.monotonic() - started,
                        "response_hex": data.hex()}, ensure_ascii=False, indent=2) + "\n")
        self.emit("protocol.finished", {"host": host, "port": port,
                                         "received_bytes": len(data), "evidence": reference})
        return data

    def tcp_probe(self, host: str, port: int, payload: bytes = b"",
                  *, timeout_seconds: float = 5) -> bytes:
        """Compatibility name for a one-response TCP exchange."""
        return self.tcp_exchange(host, port, payload, timeout_seconds=timeout_seconds)

    def start_process(self, argv: list[str], *, cwd: Path | None = None,
                      env: dict[str, str] | None = None,
                      ready_host: str | None = None, ready_port: int | None = None,
                      timeout_seconds: float = 30) -> subprocess.Popen:
        """Start a product-owned executable and stop it during SDK cleanup."""
        if not argv or any(not item for item in argv):
            raise ValueError("进程参数无效")
        self.check_cancel()
        log_path = self.output_dir / "process.log"
        log_path = self.output_dir / ("process-%d.log" % (len(self._processes) + 1))
        self._process_logs.append(log_path)
        log_handle = log_path.open("a", encoding="utf-8")
        try:
            process = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                       stdout=log_handle, stderr=subprocess.STDOUT,
                                       start_new_session=True, text=True)
        except Exception:
            log_handle.close()
            raise
        self._processes.append(process)
        self._process_handles[id(process)] = log_handle
        self.defer_cleanup(lambda: self._stop_process(
            process, self._process_handles.pop(id(process), None)))
        self.emit("process.started", {"executable": Path(argv[0]).name, "pid": process.pid})
        deadline = time.monotonic() + timeout_seconds
        if ready_host and ready_port:
            while time.monotonic() < deadline:
                self.check_cancel()
                if process.poll() is not None:
                    raise RuntimeError(f"产品进程提前退出: rc={process.returncode}")
                try:
                    with socket.create_connection((ready_host, ready_port), timeout=0.2):
                        self.emit("process.ready", {"pid": process.pid,
                                                     "host": ready_host, "port": ready_port})
                        return process
                except OSError:
                    time.sleep(0.1)
            raise TimeoutError(f"产品进程未在 {timeout_seconds:g} 秒内就绪")
        return process

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


class RegressionEngine:
    """Execute setup, business check, and cleanup with explicit verdicts."""

    def run(self, case: RegressionCase, context: CaseContext) -> CaseResult:
        started = time.monotonic()
        business = "PASS"
        reason = None
        cleanup = CleanupResult("PASS")
        context.emit("case.started", {"target": context.target})

        # Cleanup runs after every attempted setup, including blocked and
        # failed setup. Its failure is reported separately from business truth.
        try:
            context.check_cancel()
            setup = getattr(case, "setup", None)
            if callable(setup):
                context.emit("phase.started", {"phase": "setup"})
                setup(context)
                context.emit("phase.finished", {"phase": "setup"})
            context.check_cancel()
            context.emit("phase.started", {"phase": "run"})
            outcome = case.run(context)
            if outcome is False:
                business, reason = "FAIL", "用例返回失败"
            elif outcome is not True and outcome is not None:
                raise TypeError("用例必须返回 bool 或 None")
            context.emit("phase.finished", {"phase": "run", "verdict": business})
            context.check_cancel()
        except Blocked as exc:
            business, reason = "BLOCKED", str(exc)
        except Cancelled as exc:
            business, reason = "CANCELLED", str(exc)
        except CaseFailure as exc:
            business, reason = "FAIL", str(exc) or "用例断言失败"
        except AssertionError as exc:
            business, reason = "FAIL", str(exc) or "断言失败"
        except Exception as exc:  # noqa: BLE001 - executor errors are results
            business, reason = "ERROR", str(exc)

        try:
            teardown = getattr(case, "cleanup", None)
            try:
                if callable(teardown):
                    context.emit("phase.started", {"phase": "cleanup"})
                    teardown(context)
                    context.emit("phase.finished", {"phase": "cleanup"})
            finally:
                # Product fixture cleanup must run even if product teardown
                # itself fails; otherwise a setting or role can leak.
                context.cleanup_fixtures()
        except Exception as exc:  # noqa: BLE001 - cleanup must be visible
            cleanup = CleanupResult("ERROR", str(exc))
            context.emit("phase.failed", {"phase": "cleanup", "reason": str(exc)})

        verdict = "ERROR" if cleanup.status == "ERROR" else business
        result = CaseResult(
            schema_version="1.0", execution_id=context.execution_id,
            operation_id=context.operation_id,
            target=context.target, verdict=verdict,
            business_verdict=business, reason=reason, cleanup=cleanup,
            duration_seconds=time.monotonic() - started,
            evidence=context.evidence,
        )
        context.emit("case.finished", {"verdict": verdict, "business_verdict": business})
        destination = context.output_dir / "result.json"
        temporary = destination.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(result.to_dict(), ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, destination)
        return result
