"""Product-neutral regression case lifecycle and evidence recording."""

from __future__ import annotations

import contextlib
import json
import os
import re
import socket
import subprocess
import time
import uuid
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from .commands import CommandExecutor
from .contracts import (
    Blocked,
    Cancelled,
    CaseFailure,
    CaseResult,
    CleanupCase,
    CleanupResult,
    CommandResult,
    RegressionCase,
    SetupCase,
    SqlResult,
)
from .environment.context import (
    CaseEnvironment,
    EnvironmentResolver,
    NodeEndpoint,
    validate_environment,
)
from .evidence.recorder import EvidenceRecorder
from .fixtures import FixtureManager
from .sql import SqlExecutor


class CaseContext:
    """Controlled case output and cancellation surface shared by products."""

    def __init__(
        self,
        target: str,
        output_dir: Path,
        cancelled: Callable[[], bool] | None = None,
        environment: dict[str, Any] | None = None,
        operation_id: str | None = None,
        run_id: str | None = None,
    ):
        self.target = target
        self.output_dir = output_dir
        self.execution_id = uuid.uuid4().hex
        self.operation_id = operation_id
        self._cancelled = cancelled or (lambda: False)
        self.environment: CaseEnvironment = validate_environment(environment)
        # One invocation may share a run_id across case contexts so session
        # fixtures and member cases expand identical {run_id} placeholders.
        self.run_id = (
            run_id or f"run_{datetime.now():%Y%m%d_%H%M%S}_{self.execution_id[:6]}"
        )
        self.values: dict[str, Any] = {"run_id": self.run_id}
        self._evidence: list[str] = []
        self._cancel_suppressed = 0
        self._ledger: Any = None
        self._claimed = False
        self._evidence_service = EvidenceRecorder(self)
        self._commands = CommandExecutor(self)
        self._fixtures = FixtureManager(self)
        self._environment = EnvironmentResolver(self)
        self._sql = SqlExecutor(self)
        output_dir.mkdir(parents=True, exist_ok=True)
        # The directory contains only the current result. A rerun starts a new
        # event sequence; previous attachments remain unreferenced until pruned.
        (output_dir / "events.jsonl").write_text("", encoding="utf-8")

    def _claim_execution(self) -> None:
        if self._claimed:
            raise RuntimeError(
                "CaseContext belongs to one execution; create a new context"
            )
        self._claimed = True

    @property
    def evidence(self) -> tuple[str, ...]:
        return tuple(self._evidence)

    def check_cancel(self) -> None:
        if self._cancel_suppressed:
            return
        if self._cancelled():
            raise Cancelled("用例已取消")

    @contextlib.contextmanager
    def suppress_cancellation(self):
        """Allow restoration commands to run while a run is being cancelled.

        Deferred cleanups and product teardown still need ``context.command``
        (e.g. ``pg_ctl stop``) after cancellation; without suppression every
        restoration attempt would raise ``Cancelled`` at its first command and
        leak the very resources cleanup exists to release.
        """
        self._cancel_suppressed += 1
        try:
            yield
        finally:
            self._cancel_suppressed -= 1

    @property
    def ledger(self):
        """Resource ownership must survive artifact cleanup and process crashes."""
        if self._ledger is None:
            from .ledger import ResourceLedger
            root = self.environment.get("ledger_root")
            if root is None:
                raise Blocked("外部资源操作需要持久化 ledger_root")
            self._ledger = ResourceLedger(Path(root))
        return self._ledger

    def defer_cleanup(self, action: Callable[[], None], *, priority: int = 0) -> None:
        return self._fixtures.defer_cleanup(action, priority=priority)

    def stop_processes(self) -> None:
        return self._commands.stop_processes()

    def cleanup_fixtures(self) -> None:
        return self._fixtures.cleanup_fixtures()

    _ENV_PLACEHOLDER = re.compile(r"\{env\.([A-Za-z_][A-Za-z0-9_.]*)\}")
    _NODE_PLACEHOLDER = re.compile(
        r"\{node\.([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)\}"
    )

    def expand(self, value: Any) -> Any:
        return self._environment.expand(value)

    def resolve_node(self, selector: str = "primary") -> str:
        return self._environment.resolve_node(selector)

    def node_endpoint(self, selector: str = "primary") -> NodeEndpoint:
        return self._environment.node_endpoint(selector)

    @staticmethod
    def is_local(host: Any) -> bool:
        host = str(host)
        if host in {"127.0.0.1", "localhost", "local", "::1"}:
            return True
        try:
            local_addresses = {
                item[4][0] for item in socket.getaddrinfo(socket.gethostname(), None)
            }
            target_addresses = {item[4][0] for item in socket.getaddrinfo(host, None)}
            return bool(local_addresses & target_addresses)
        except socket.gaierror:
            return False

    def reload(self, node: str, *, user: str | None = None) -> None:
        return self._fixtures.reload(node, user=user)

    def set_setting(
        self, node: str, name: str, value: str, *, user: str | None = None
    ) -> None:
        return self._fixtures.set_setting(node, name, value, user=user)

    def create_role(self, node: str, name: str, attributes: str = "") -> None:
        return self._fixtures.create_role(node, name, attributes)

    def defer_drop_table(self, node: str, name: str) -> None:
        return self._fixtures.defer_drop_table(node, name)

    def next_evidence_key(self, prefix: str) -> str:
        """Allocate a unique operation key within this execution."""
        return self._evidence_service.next_operation_key(prefix)

    def emit(self, kind: str, payload: dict[str, Any]) -> None:
        return self._evidence_service.emit(kind, payload)

    def step(
        self,
        key: str,
        title: str,
        *,
        status: str = "PASS",
        details: dict[str, Any] | None = None,
    ) -> None:
        return self._evidence_service.step(key, title, status=status, details=details)

    def check(
        self,
        key: str,
        title: str,
        passed: bool,
        *,
        expected: Any = None,
        actual: Any = None,
        details: dict[str, Any] | None = None,
        reason: str | None = None,
    ) -> None:
        """Record a business assertion and fail the case when it is false."""
        if not isinstance(passed, bool):
            raise TypeError("assertion outcome must be bool")
        payload = dict(details or {})
        payload.update(expected=expected, actual=actual)
        self.step(key, title, status="PASS" if passed else "FAIL", details=payload)
        if not passed:
            raise CaseFailure(reason or f"{title}: 预期 {expected}，实际 {actual}")

    def attach_text(self, name: str, content: str) -> str:
        return self._evidence_service.attach_text(name, content)

    def attach_bytes(self, name: str, content: bytes) -> str:
        return self._evidence_service.attach_bytes(name, content)

    def attach_file(self, name: str, source: Path) -> str:
        return self._evidence_service.attach_file(name, source)

    def sql(
        self,
        node: str,
        query: str,
        *,
        database: str = "postgres",
        user: str | None = None,
        password: str | None = None,
        parameters=None,
        statement_timeout_seconds: float | None = 10,
        preserve_types: bool = False,
    ) -> SqlResult:
        return self._sql.sql(
            node,
            query,
            database=database,
            user=user,
            password=password,
            parameters=parameters,
            statement_timeout_seconds=statement_timeout_seconds,
            preserve_types=preserve_types,
        )

    def sql_session(
        self,
        node: str,
        *,
        database: str = "postgres",
        user: str | None = None,
        password: str | None = None,
        connect_timeout_seconds: int = 5,
        statement_timeout_seconds: float | None = 10,
        preserve_types: bool = False,
    ):
        return self._sql.session(
            node,
            database=database,
            user=user,
            password=password,
            connect_timeout_seconds=connect_timeout_seconds,
            statement_timeout_seconds=statement_timeout_seconds,
            preserve_types=preserve_types,
        )

    def command(
        self,
        argv: list[str],
        *,
        cwd: Path | None = None,
        timeout_seconds: float | None = 30,
        input_text: str | None = None,
        merge_stderr: bool = False,
    ) -> CommandResult:
        return self._commands.command(
            argv,
            cwd=cwd,
            timeout_seconds=timeout_seconds,
            input_text=input_text,
            merge_stderr=merge_stderr,
        )

    def start_command(
        self, argv: list[str], input_text: str | None = None
    ) -> subprocess.Popen:
        return self._commands.start_command(argv, input_text)

    def finish_command(
        self,
        argv: list[str],
        process: subprocess.Popen,
        timeout_seconds: float | None = None,
        input_text: str | None = None,
    ) -> subprocess.CompletedProcess:
        return self._commands.finish_command(argv, process, timeout_seconds, input_text)

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
        return self._commands.tcp_exchange(
            host,
            port,
            payload,
            timeout_seconds=timeout_seconds,
            expected_bytes=expected_bytes,
            until_eof=until_eof,
        )

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
        return self._commands.start_process(
            argv,
            cwd=cwd,
            env=env,
            ready_host=ready_host,
            ready_port=ready_port,
            timeout_seconds=timeout_seconds,
        )


class RegressionEngine:
    """Execute setup, business check, and cleanup with explicit verdicts."""

    def run(self, case: RegressionCase, context: CaseContext) -> CaseResult:
        context._claim_execution()
        started = time.monotonic()
        business = "PASS"
        reason = None
        cleanup = CleanupResult("PASS")
        context.emit("case.started", {"target": context.target})

        # Cleanup runs after every attempted setup, including blocked and
        # failed setup. Its failure is reported separately from business truth.
        try:
            description=getattr(case,'report_description',None)
            if isinstance(description,dict):
                context.attach_text('case-description.json',json.dumps(description,ensure_ascii=False,indent=2)+'\n')
            context.check_cancel()
            if isinstance(case, SetupCase):
                context.emit("phase.started", {"phase": "setup"})
                case.setup(context)
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
            try:
                if isinstance(case, CleanupCase):
                    context.emit("phase.started", {"phase": "cleanup"})
                    with context.suppress_cancellation():
                        case.cleanup(context)
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
            schema_version="1.0",
            execution_id=context.execution_id,
            operation_id=context.operation_id,
            target=context.target,
            verdict=verdict,
            business_verdict=business,
            reason=reason,
            cleanup=cleanup,
            duration_seconds=time.monotonic() - started,
            evidence=context.evidence,
        )
        context.emit(
            "case.finished", {"verdict": verdict, "business_verdict": business}
        )
        destination = context.output_dir / "result.json"
        temporary = destination.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(result.to_dict(), ensure_ascii=False, sort_keys=True, indent=2)
            + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, destination)
        return result
