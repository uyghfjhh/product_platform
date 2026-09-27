"""Product-neutral regression case lifecycle and evidence recording."""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
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


class CaseContext:
    """Controlled case output and cancellation surface shared by products."""

    def __init__(self, target: str, output_dir: Path,
                 cancelled: Callable[[], bool] | None = None,
                 environment: dict[str, Any] | None = None,
                 operation_id: str | None = None):
        self.target = target
        self.output_dir = output_dir
        self.execution_id = uuid.uuid4().hex
        self.operation_id = operation_id
        self._cancelled = cancelled or (lambda: False)
        self.environment = environment or {}
        self._sequence = 0
        self._sql_sequence = 0
        self._evidence: list[str] = []
        self._cleanup_actions: list[Callable[[], None]] = []
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

    def defer_cleanup(self, action: Callable[[], None]) -> None:
        """Register an idempotent product fixture cleanup action."""
        self._cleanup_actions.append(action)

    def cleanup_fixtures(self) -> None:
        """Run registered fixture cleanup in reverse order."""
        errors = []
        for action in reversed(self._cleanup_actions):
            try:
                action()
            except Exception as exc:
                errors.append(str(exc))
        self._cleanup_actions.clear()
        if errors:
            raise RuntimeError("; ".join(errors))

    def reload(self, node: str) -> None:
        """Reload PostgreSQL configuration through the declared node."""
        self.sql(node, "SELECT pg_reload_conf()")
        self.step("fixture-reload", "重载数据库配置")

    def set_setting(self, node: str, name: str, value: str) -> None:
        """Set a runtime setting and restore its previous value afterwards."""
        if not name.replace("_", "").replace(".", "").isalnum():
            raise ValueError("配置参数名无效")
        old = self.sql(node, f"SELECT current_setting('{name}', true)").rows
        old_value = old[0][0] if old and old[0] else None
        escaped = value.replace("'", "''")
        self.sql(node, f"ALTER SYSTEM SET {name} = '{escaped}'")
        self.reload(node)
        restore = "RESET" if old_value is None else f"SET {name} = '{old_value.replace(chr(39), chr(39) * 2)}'"
        self.defer_cleanup(lambda: (self.sql(node, f"ALTER SYSTEM {restore}"), self.reload(node)))

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
            user: str | None = None) -> SqlResult:
        """Execute one SQL statement against a declared product node.

        SQL text and returned rows become evidence. Product cases own the
        expected values and assertions; connection setup stays in this SDK.
        """
        self.check_cancel()
        nodes = self.environment.get("nodes") or {}
        endpoint = nodes.get(node)
        if not isinstance(endpoint, dict):
            raise Blocked(f"未配置测试节点: {node}")
        host, port = endpoint.get("host"), endpoint.get("port")
        if not isinstance(host, str) or not isinstance(port, int):
            raise Blocked(f"测试节点连接信息无效: {node}")
        self._sql_sequence += 1
        key = f"sql-{self._sql_sequence}"
        users = self.environment.get("users") or {}
        selected_user = user or self.environment.get("user") or "postgres"
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
                timeout_seconds: float = 30) -> CommandResult:
        """Run a product command without a shell and retain its output as evidence.

        A nonzero exit is returned to the case, which owns the business
        assertion. Timeout and cancellation interrupt the whole process group.
        """
        if not argv or any(not isinstance(arg, str) or not arg for arg in argv):
            raise ValueError("命令必须是非空字符串参数数组")
        if timeout_seconds <= 0:
            raise ValueError("命令超时必须大于零")
        self.check_cancel()
        self._sql_sequence += 1
        key = f"command-{self._sql_sequence}"
        self.emit("command.started", {"step_key": key, "executable": Path(argv[0]).name})
        started = time.monotonic()
        try:
            process = subprocess.Popen(
                argv, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, start_new_session=True,
            )
        except OSError as exc:
            self.emit("command.failed", {"step_key": key, "reason": str(exc)})
            raise
        try:
            while True:
                self.check_cancel()
                remaining = timeout_seconds - (time.monotonic() - started)
                if remaining <= 0:
                    raise TimeoutError(f"命令超过 {timeout_seconds:g} 秒")
                try:
                    stdout, stderr = process.communicate(timeout=min(remaining, 0.2))
                    break
                except subprocess.TimeoutExpired:
                    continue
        except (Cancelled, TimeoutError) as exc:
            # A case can launch child processes; stop the process group so a
            # timed-out test cannot keep modifying its environment afterward.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            stdout, stderr = process.communicate()
            reference = self.attach_text(key + ".json", json.dumps({
                "stdout": stdout, "stderr": stderr, "error": str(exc),
            }, ensure_ascii=False, indent=2) + "\n")
            self.emit("command.failed", {"step_key": key, "reason": str(exc), "evidence": reference})
            raise
        result = CommandResult(process.returncode, stdout, stderr, time.monotonic() - started)
        reference = self.attach_text(key + ".json", json.dumps({
            "returncode": result.returncode, "stdout": stdout, "stderr": stderr,
            "duration_seconds": result.duration_seconds,
        }, ensure_ascii=False, indent=2) + "\n")
        self.emit("command.finished", {
            "step_key": key, "returncode": result.returncode, "evidence": reference,
        })
        self.check_cancel()
        return result


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
