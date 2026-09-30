"""SQL sessions, transactions and evidence with explicit connection ownership."""

from __future__ import annotations

import json
from contextlib import contextmanager
from typing import TYPE_CHECKING

import psycopg

from .contracts import Blocked, Cancelled, SqlResult

if TYPE_CHECKING:
    from .engine import CaseContext


class SqlSession:
    """A scoped SQL connection. Closing a session makes further use invalid."""

    def __init__(
        self,
        executor: SqlExecutor,
        connection,
        node: str,
        database: str,
        user: str,
        preserve_types: bool,
    ):
        self._executor = executor
        self._connection = connection
        self.node, self.database, self.user = node, database, user
        self.preserve_types = preserve_types
        self._closed = False

    def execute(self, query: str, parameters=None) -> SqlResult:
        if self._closed:
            raise RuntimeError("SQL session is closed")
        return self._executor.execute(self, query, parameters)

    @contextmanager
    def transaction(self):
        if self._closed:
            raise RuntimeError("SQL session is closed")
        self._executor.context.check_cancel()
        with self._connection.transaction():
            yield self
            self._executor.context.check_cancel()


class SqlExecutor:
    def __init__(self, context: CaseContext):
        self.context = context
        self._attempts = 0

    @contextmanager
    def session(
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
        if connect_timeout_seconds <= 0:
            raise ValueError("connection timeout must be positive")
        if statement_timeout_seconds is not None and statement_timeout_seconds <= 0:
            raise ValueError("statement timeout must be positive or None")
        self.context.check_cancel()
        endpoint = self.context.node_endpoint(node)
        host, port = endpoint.get("host"), endpoint.get("port")
        if (
            not isinstance(host, str)
            or not isinstance(port, int)
            or isinstance(port, bool)
        ):
            raise Blocked(f"测试节点连接信息无效: {node}")
        users = self.context.environment.get("users") or {}
        selected_user = user or self.context.environment.get("user") or "postgres"
        selected_password = password
        if selected_password is None:
            selected_password = users.get(selected_user, {}).get("password")
        milliseconds = (
            0
            if statement_timeout_seconds is None
            else max(1, int(statement_timeout_seconds * 1000))
        )
        with psycopg.connect(
            host=host,
            port=port,
            dbname=database,
            user=selected_user,
            password=selected_password,
            connect_timeout=connect_timeout_seconds,
            options=f"-c statement_timeout={milliseconds}",
            autocommit=True,
        ) as connection:
            session = SqlSession(
                self, connection, node, database, selected_user, preserve_types
            )
            try:
                yield session
            finally:
                session._closed = True

    def execute(self, session: SqlSession, query: str, parameters=None) -> SqlResult:
        context = self.context
        context.check_cancel()
        parameter_evidence = {} if parameters is None else {"parameters": parameters}
        self._attempts += 1
        key = context.next_evidence_key("sql")
        context.emit(
            "sql.started",
            {
                "step_key": key,
                "node": session.node,
                "database": session.database,
                "user": session.user,
            },
        )
        try:
            with session._connection.cursor() as cursor:
                if parameters is None:
                    cursor.execute(query)
                else:
                    cursor.execute(query, parameters)
                rows = cursor.fetchall() if cursor.description else []
                columns = (
                    tuple(item.name for item in cursor.description)
                    if cursor.description
                    else ()
                )
                tag = cursor.statusmessage or ""
        except Exception as exc:
            reference = context.attach_text(
                key + ".json",
                json.dumps(
                    {
                        "node": session.node,
                        "database": session.database,
                        "sql": query,
                        "sqlstate": getattr(exc, "sqlstate", None),
                        "error": str(exc),
                        **parameter_evidence,
                    },
                    ensure_ascii=False,
                    indent=2,
                    default=str,
                )
                + "\n",
            )
            context.emit(
                "sql.failed",
                {
                    "step_key": key,
                    "node": session.node,
                    "reason": str(exc),
                    "evidence": reference,
                },
            )
            raise
        normalized = tuple(
            tuple(
                value if session.preserve_types or value is None else str(value)
                for value in row
            )
            for row in rows
        )
        result = SqlResult(normalized, columns, tag)
        reference = context.attach_text(
            key + ".json",
            json.dumps(
                {
                    "node": session.node,
                    "database": session.database,
                    "sql": query,
                    "columns": columns,
                    "rows": normalized,
                    "command_tag": tag,
                    **parameter_evidence,
                },
                ensure_ascii=False,
                indent=2,
                default=str,
            )
            + "\n",
        )
        context.emit(
            "sql.finished",
            {
                "step_key": key,
                "node": session.node,
                "rows": len(rows),
                "evidence": reference,
            },
        )
        context.check_cancel()
        return result

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
        # Connection failures must also produce query evidence.
        before = self._attempts
        try:
            with self.session(
                node,
                database=database,
                user=user,
                password=password,
                statement_timeout_seconds=statement_timeout_seconds,
                preserve_types=preserve_types,
            ) as session:
                return session.execute(query, parameters)
        except Exception as exc:
            if self._attempts == before and not isinstance(
                exc, (Blocked, Cancelled, ValueError)
            ):
                self._attempts += 1
                key = self.context.next_evidence_key("sql")
                self.context.emit(
                    "sql.started",
                    {
                        "step_key": key,
                        "node": node,
                        "database": database,
                        "user": user
                        or self.context.environment.get("user")
                        or "postgres",
                    },
                )
                reference = self.context.attach_text(
                    key + ".json",
                    json.dumps(
                        {
                            "node": node,
                            "database": database,
                            "sql": query,
                            "sqlstate": getattr(exc, "sqlstate", None),
                            "error": str(exc),
                        },
                        ensure_ascii=False,
                        indent=2,
                    )
                    + "\n",
                )
                self.context.emit(
                    "sql.failed",
                    {
                        "step_key": key,
                        "node": node,
                        "reason": str(exc),
                        "evidence": reference,
                    },
                )
            raise
