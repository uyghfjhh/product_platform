"""psql execution and exported-SQL step wiring (legacy PostgresClient shape)."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from ..commands import display_command
from ..engine import CaseContext
from .base import (_NULL_TOKEN, _SQLSTATE, DEFAULT_COMMAND_TIMEOUT,
                   StepExecutionResult, _parse_csv, _render_aligned, step_user)


def _db_binary(context: CaseContext, name: str) -> str:
    bin_dir = context.environment.get("db_bin_dir")
    return str(Path(bin_dir) / name) if bin_dir else name


def execute_psql(context: CaseContext, node: str, sql: str, *,
                 user: str = "postgres", database: str = "postgres",
                 structured: bool = False,
                 timeout: float | None = None,
                 client_encoding: str | None = None,
                 password: str | None = None,
                 connection: dict | None = None,
                 command: list | None = None) -> StepExecutionResult:
    """Run psql exactly like the legacy PostgresClient.

    stderr folds into stdout, ``--csv`` + ``-P null=__FBASE_REGRESS_NULL__``
    parses structured results while keeping NOTICE diagnostics in the
    rendered output, and verbose ERROR lines provide SQLSTATE/message.  The
    psql process runs locally and connects over the wire, matching the
    legacy ``command_runner.run`` behaviour including the rc=124 timeout
    convention.
    """
    endpoint = context.node_endpoint(node)
    connection = connection or {}
    argv = [
        _db_binary(context, "psql"), "-X", "-v", "ON_ERROR_STOP=1",
        "-v", "VERBOSITY=verbose", "-P", "pager=off",
        "-h", str(connection.get("host", endpoint["host"])),
        "-p", str(connection.get("port", endpoint["port"])),
        "-U", user, "-d", database,
    ]
    if structured:
        argv.extend(["--csv", "-P", "null=%s" % _NULL_TOKEN])
    argv.extend(["-c", sql])
    environment = []
    if client_encoding:
        environment.append("PGCLIENTENCODING=%s" % client_encoding)
    if password is not None:
        environment.append("PGPASSWORD=%s" % password)
    if environment:
        argv = ["env"] + environment + argv
    effective_timeout = timeout if timeout is not None else DEFAULT_COMMAND_TIMEOUT
    if command is not None:
        argv = command
    result = None
    try:
        result = context.command(argv, timeout_seconds=effective_timeout,
                                 merge_stderr=True)
        returncode, output = result.returncode, result.stdout
    except TimeoutError as exc:
        returncode = 124
        output = (getattr(exc, "partial_stdout", "") or
                  "") + f"\n命令执行超时（{effective_timeout}s）"
    match = _SQLSTATE.search(output)
    columns: list = []
    rows: list = []
    display = output
    if structured and returncode == 0:
        try:
            columns, rows, diagnostics = _parse_csv(output)
            aligned = _render_aligned(columns, rows)
            display = ("%s\n%s" % (diagnostics, aligned)
                       if diagnostics else aligned)
        except (csv.Error, IndexError, ValueError):
            columns, rows = [], []
    return StepExecutionResult(
        returncode, output=output, display_output=display, command=argv,
        display_command=display_command(argv),
        columns=columns, rows=rows,
        sqlstate=match.group(1) if match else None,
        error_message=match.group(2).strip() if match else None,
        evidence=getattr(result, "evidence", None))


def _sql_execution(context: CaseContext, step: dict[str, Any],
                   definition: dict[str, Any] | None):
    """Run one exported SQL step through psql with legacy arguments."""
    node = context.resolve_node(step.get("node", "primary"))
    assertion = step.get("assertion") or {}
    structured = assertion.get("type") in {
        "rows_equal", "rows_with_output_contains", "query_equals", "scalar_equals",
        "scalar_integer",
    }
    connection = step.get("connection") or (definition or {}).get("connection")
    if connection:
        connection = context.expand(connection)
    execution = execute_psql(
        context, node, step["sql"],
        user=step_user(context, step) or context.environment.get("user") or "postgres",
        database=step.get("database") or "postgres",
        structured=structured,
        timeout=step.get("command_timeout"),
        client_encoding=step.get("client_encoding"),
        password=step.get("password"),
        connection=connection)
    return node, execution
