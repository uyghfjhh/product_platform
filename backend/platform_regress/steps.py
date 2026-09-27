"""Product-neutral assertions for exported declarative SQL steps."""

from __future__ import annotations

import csv
import io
import os
import re
import shlex
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable
import time

import psycopg

from .engine import Blocked, Cancelled, CaseContext


SUPPORTED_SQL_ASSERTIONS = frozenset({
    "rows_equal", "rows_with_output_contains", "query_equals", "scalar_equals",
    "output_contains_text", "output_contains", "sql_error", "sql_fails",
    "command_succeeds",
})

SUPPORTED_COMMAND_ASSERTIONS = frozenset({
    "command_succeeds", "command_fails", "output_contains", "output_contains_text",
})

# Legacy executors bound a default command timeout of five minutes.
DEFAULT_COMMAND_TIMEOUT = 300.0

_SQLSTATE = re.compile(r"ERROR:\s+([0-9A-Z]{5}):\s+([^\n]+)")
_NULL = "__FBASE_REGRESS_NULL__"
_PSQL_DIAGNOSTIC = re.compile(
    r"^(WARNING|NOTICE|INFO|DETAIL|HINT|CONTEXT|LOCATION):")


def meaningful_lines(output: str) -> list[str]:
    """Strip blank and password-expiry noise lines like the legacy executor."""
    result = []
    for line in (output or "").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("Password will expire at "):
            continue
        result.append(stripped)
    return result


def format_psql_output(output: str) -> str:
    lines = []
    for line in (output or "").splitlines():
        if line.strip().startswith("Password will expire at "):
            continue
        lines.append(line.rstrip())
    return "\n".join(lines).strip("\n") or "<空>"


@dataclass
class StepExecutionResult:
    """Structured outcome of one exported step, mirroring the legacy model."""

    returncode: int
    output: str = ""
    display_output: str = ""
    command: Any = None
    columns: list = field(default_factory=list)
    rows: list = field(default_factory=list)
    sqlstate: str | None = None
    error_message: str | None = None


def _render_aligned(columns: list, rows: list) -> str:
    """Render psql-aligned output exactly like the legacy executor."""
    if not columns:
        return "<空>"
    text_rows = [["" if value is None else str(value) for value in row]
                 for row in rows]

    def display_width(value):
        return sum(2 if unicodedata.east_asian_width(char) in ("W", "F") else 1
                   for char in value)

    widths = []
    for index, column in enumerate(columns):
        values = [str(column)] + [row[index] if index < len(row) else ""
                                  for row in text_rows]
        widths.append(max(display_width(value) for value in values))

    def render_row(values):
        padded = []
        for index, width in enumerate(widths):
            value = values[index] if index < len(values) else ""
            padded.append(value + " " * (width - display_width(value)))
        return " " + " | ".join(padded).rstrip()

    lines = [render_row([str(value) for value in columns])]
    lines.append("-" + "-+-".join("-" * width for width in widths))
    lines.extend(render_row(row) for row in text_rows)
    count = len(rows)
    lines.append("(%s row%s)" % (count, "" if count == 1 else "s"))
    return "\n".join(lines)


def _parse_csv(output: str) -> tuple[list, list, str]:
    """Parse psql CSV after preserving server NOTICE/WARNING diagnostics."""
    lines = [
        line for line in (output or "").splitlines()
        if not line.strip().startswith("Password will expire at ")]
    diagnostics = []
    in_diagnostic = False
    while lines:
        stripped = lines[0].strip()
        if _PSQL_DIAGNOSTIC.match(stripped):
            in_diagnostic = True
            diagnostics.append(lines.pop(0))
        elif in_diagnostic and lines[0][:1].isspace():
            diagnostics.append(lines.pop(0))
        else:
            break
    records = list(csv.reader(io.StringIO("\n".join(lines))))
    if not records:
        return [], [], "\n".join(diagnostics)
    columns = records[0]
    rows = []
    for record in records[1:]:
        rows.append([None if value == _NULL else value for value in record])
    return columns, rows, "\n".join(diagnostics)


def _fbase_binary(context: CaseContext, name: str) -> str:
    bin_dir = context.environment.get("fbase_bin_dir")
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
        _fbase_binary(context, "psql"), "-X", "-v", "ON_ERROR_STOP=1",
        "-v", "VERBOSITY=verbose", "-P", "pager=off",
        "-h", str(connection.get("host", endpoint["host"])),
        "-p", str(connection.get("port", endpoint["port"])),
        "-U", user, "-d", database,
    ]
    if structured:
        argv.extend(["--csv", "-P", "null=%s" % _NULL])
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
    try:
        result = context.command(argv, timeout_seconds=effective_timeout,
                                 merge_stderr=True)
        returncode, output = result.returncode, result.stdout
    except TimeoutError as exc:
        returncode = 124
        output = (getattr(exc, "partial_stdout", "") or
                  "") + "\n命令执行超时（%ss）" % effective_timeout
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
        columns=columns, rows=rows,
        sqlstate=match.group(1) if match else None,
        error_message=match.group(2).strip() if match else None)


def evaluate_assertion(assertion: dict[str, Any],
                       result: StepExecutionResult) -> tuple[bool, str, str]:
    """Evaluate every exported assertion exactly like the legacy evaluator."""
    kind = (assertion or {}).get("type")
    if kind == "rows_equal":
        expected = assertion["rows"]
        passed = result.returncode == 0 and result.rows == expected

        def rows_text(rows):
            return "; ".join(
                "|".join("" if value is None else str(value) for value in row)
                for row in rows) or "<空>"
        actual = "返回行=%s，退出码=%s" % (rows_text(result.rows), result.returncode)
        reason = ("" if passed else
                  "预期返回行=%s；实际%s" % (rows_text(expected), actual))
        return passed, actual, reason
    if kind == "rows_with_output_contains":
        expected = assertion["rows"]
        marker = assertion["value"]
        passed = (result.returncode == 0 and result.rows == expected and
                  marker in (result.output or ""))
        rows_text = "; ".join(
            "|".join("" if value is None else str(value) for value in row)
            for row in result.rows) or "<空>"
        actual = "返回行=%s，输出包含=%s，退出码=%s" % (
            rows_text,
            marker if marker in (result.output or "") else "<未匹配>",
            result.returncode)
        reason = ("" if passed else
                  "预期返回行=%s 且输出包含 %s；实际%s" % (expected, marker, actual))
        return passed, actual, reason
    if kind == "query_equals":
        actual_value = ("|".join("" if value is None else str(value)
                                 for value in result.rows[-1])
                        if result.rows else "")
        passed = result.returncode == 0 and actual_value == assertion["value"]
        actual = "返回值=%s，退出码=%s" % (actual_value or "<空>", result.returncode)
        reason = ("" if passed else
                  "预期返回值=%s；实际%s" % (assertion["value"], actual))
        return passed, actual, reason
    if kind == "scalar_equals":
        actual_value = (result.rows[0][0]
                        if len(result.rows) == 1 and len(result.rows[0]) == 1
                        else None)
        passed = result.returncode == 0 and actual_value == assertion["value"]
        actual = "返回值=%s，退出码=%s" % (actual_value, result.returncode)
        reason = ("" if passed else
                  "预期返回值=%s；实际%s" % (assertion["value"], actual))
        return passed, actual, reason
    if kind == "sql_error":
        sqlstate = result.sqlstate or "unknown"
        message = result.error_message or "未捕获 ERROR"
        passed = (result.returncode != 0 and sqlstate == assertion["sqlstate"]
                  and assertion["message_contains"] in message)
        actual = "SQLSTATE=%s，错误=%s，退出码=%s" % (
            sqlstate, message, result.returncode)
        reason = ("" if passed else
                  "预期 SQLSTATE=%s 且错误包含 %s；实际%s" %
                  (assertion["sqlstate"], assertion["message_contains"], actual))
        return passed, actual, reason
    if kind == "sql_fails":
        sqlstate = result.sqlstate or "unknown"
        message = result.error_message or "未捕获 ERROR"
        passed = (result.returncode != 0 and
                  assertion["message_contains"] in message)
        actual = "SQLSTATE=%s，错误=%s，退出码=%s" % (
            sqlstate, message, result.returncode)
        reason = ("" if passed else
                  "预期 SQL 执行失败且错误包含 %s；实际%s" %
                  (assertion["message_contains"], actual))
        return passed, actual, reason
    if kind == "output_contains":
        lines = meaningful_lines(result.output)
        missing = [value for value in assertion["values"] if value not in lines]
        passed = result.returncode == 0 and not missing
        actual = "输出=%s，退出码=%s" % (" | ".join(lines) or "<空>", result.returncode)
        reason = ("" if passed else
                  "预期输出包含%s；实际%s" % (assertion["values"], actual))
        return passed, actual, reason
    if kind == "output_contains_text":
        output = result.output or ""
        missing = [value for value in assertion["values"] if value not in output]
        passed = result.returncode == 0 and not missing
        actual = "输出=%s，退出码=%s" % (format_psql_output(output), result.returncode)
        reason = ("" if passed else
                  "预期输出包含%s；实际%s" % (assertion["values"], actual))
        return passed, actual, reason
    if kind == "command_succeeds":
        passed = result.returncode == 0
        actual = "退出码=%s，输出=%s" % (
            result.returncode, format_psql_output(result.output))
        return passed, actual, ("" if passed else "预期退出码=0；实际%s" % actual)
    if kind == "command_fails":
        text = format_psql_output(result.output)
        passed = (result.returncode != 0 and
                  assertion["message_contains"] in text)
        actual = "退出码=%s，输出=%s" % (result.returncode, text)
        reason = ("" if passed else
                  "预期退出码非 0 且输出包含 %s；实际%s" %
                  (assertion["message_contains"], actual))
        return passed, actual, reason
    return False, "未知断言类型=%s" % kind, "框架不支持该断言类型"


def evaluate_command_assertion(assertion: dict[str, Any], returncode: int,
                               output: str) -> tuple[bool, str, str]:
    """Evaluate a command-step assertion exactly like the legacy executor."""
    return evaluate_assertion(
        assertion, StepExecutionResult(returncode, output=output))


def run_sql_step(context: CaseContext, step: dict[str, Any], index: int,
                 node: str) -> None:
    """Execute one SQL step and preserve expected errors as business facts.

    Only declared assertion shapes are accepted. An unsupported shape is an
    executor error, never an implicit PASS or a guessed assertion.
    """
    assertion = step.get("assertion") or {}
    kind = assertion.get("type")
    if step.get("type") == "cluster_action":
        if step.get("action") != "reload" or kind != "command_succeeds":
            raise ValueError(f"平台不支持集群动作: {step.get('action')}")
        context.reload(node)
        context.step(f"step-{index}", step["title"], details={"action": "reload"})
        return
    if step.get("type") == "wait_sql":
        deadline = time.monotonic() + float(step.get("timeout", 30))
        interval = float(step.get("interval", 1))
        while True:
            try:
                result = context.sql(node, step["sql"], database=step.get("database") or "postgres",
                                     user=step.get("user"), password=step.get("password"))
                expected = tuple(tuple(str(cell) for cell in row) for row in assertion.get("rows", []))
                if result.rows == expected:
                    context.step(f"step-{index}", step["title"], details={"poll": "matched"})
                    return
            except psycopg.Error:
                pass
            if time.monotonic() >= deadline:
                context.step(f"step-{index}", step["title"], status="FAIL",
                             details={"timeout": step.get("timeout", 30)})
                raise AssertionError(f"{step['title']}: 轮询超时")
            context.check_cancel()
            time.sleep(interval)
    if step.get("type") != "sql" or kind not in SUPPORTED_SQL_ASSERTIONS:
        raise ValueError(f"平台不支持 SQL 步骤或断言: {kind}")
    key = f"step-{index}"
    title = step["title"]
    query = step["sql"]
    database = step.get("database") or "postgres"
    sql_kwargs = {"database": database}
    if step.get("user"):
        sql_kwargs["user"] = step["user"]
    if step.get("password"):
        sql_kwargs["password"] = step["password"]
    if kind in {"sql_error", "sql_fails"}:
        try:
            context.sql(node, query, **sql_kwargs)
        except psycopg.Error as exc:
            actual = str(exc)
            state = getattr(exc, "sqlstate", None)
            expected = assertion["message_contains"]
            passed = expected in actual and (kind != "sql_error" or state == assertion["sqlstate"])
            details = {"expected_error": expected, "actual_error": actual, "sqlstate": state}
            context.step(key, title, status="PASS" if passed else "FAIL", details=details)
            if not passed:
                raise AssertionError(f"{title}: SQL 错误与预期不符") from exc
            return
        context.step(key, title, status="FAIL", details={"expected_error": assertion["message_contains"],
                                                       "actual": "SQL 成功"})
        raise AssertionError(f"{title}: SQL 意外成功")

    result = context.sql(node, query, **sql_kwargs)
    if kind == "rows_equal":
        expected_rows = tuple(tuple(None if cell is None else str(cell) for cell in row)
                              for row in assertion["rows"])
        passed = result.rows == expected_rows
        details = {"expected": expected_rows, "actual": result.rows}
    elif kind in {"output_contains_text", "output_contains"}:
        # The structured SQL result contains the values that psql previously
        # printed. Include columns and the command tag for metadata checks.
        display = "\n".join([*result.columns,
                             *("|".join("" if value is None else value for value in row)
                               for row in result.rows), result.command_tag])
        missing = [value for value in assertion["values"] if value not in display]
        passed = not missing
        details = {"missing": missing, "actual": display}
    else:
        passed = True
        details = {"command_tag": result.command_tag}
    context.step(key, title, status="PASS" if passed else "FAIL", details=details)
    if not passed:
        raise AssertionError(f"{title}: SQL 断言失败")


def _shell_command(argv: list[str], cwd: str | None,
                   env: dict[str, Any] | None) -> str:
    """Render an argv plus cwd/env into one shell line for local or ssh use."""
    parts = []
    if cwd:
        parts.extend(["cd", shlex.quote(str(cwd)), "&&"])
    if env:
        parts.append("env")
        parts.extend("%s=%s" % (key, shlex.quote(str(value)))
                     for key, value in sorted(env.items()))
    parts.extend(shlex.quote(value) for value in argv)
    return " ".join(parts)


def _record_step(context: CaseContext, key: str, title: str, passed: bool,
                 actual: str, output: str, reason: str,
                 node: str | None = None) -> None:
    """Record a step verdict and raise the legacy failure signal."""
    details = {"actual": actual, "output": output, "reason": reason}
    if node is not None:
        details["node"] = node
    context.step(key, title, status="PASS" if passed else "FAIL", details=details)
    if not passed:
        raise AssertionError(reason or actual or "断言失败")


def run_command_step(context: CaseContext, step: dict[str, Any], index: int,
                     before_command: Callable[[list[str]], None] | None = None) -> None:
    """Execute one exported command step through the node's transport.

    Local nodes run the argv directly (or via ``sh -lc`` when cwd/env are
    declared); remote nodes run through the system ssh client — the same
    shape the legacy executor used, including merged stderr and the rc=124
    timeout convention so ``command_fails`` assertions keep their meaning.
    """
    assertion = step.get("assertion") or {}
    key = f"step-{index}"
    title = step.get("title") or f"step {index}"
    node = context.resolve_node(step.get("node") or "primary")
    endpoint = context.node_endpoint(node)
    argv = [str(item) for item in step.get("argv") or []]
    if not argv:
        raise ValueError("command 步骤缺少 argv")
    if before_command is not None:
        before_command(argv)
    timeout = step.get("timeout")
    timeout_seconds = float(timeout) if timeout is not None else DEFAULT_COMMAND_TIMEOUT
    host = endpoint.get("host")
    cwd, env = step.get("cwd"), step.get("env")
    if context.is_local(host):
        run_argv = (["sh", "-lc", _shell_command(argv, cwd, env)]
                    if (cwd or env) else argv)
    else:
        cluster = context.environment.get("cluster")
        transport = context.environment.get("transport") or {}
        if isinstance(cluster, dict):
            transport = cluster.get("transport") or transport
        ssh_user = (transport.get("ssh_user") or context.environment.get("ssh_user") or
                    os.environ.get("USER") or "postgres")
        ssh_port = transport.get("ssh_port") or context.environment.get("ssh_port") or 22
        run_argv = ["ssh", "-p", str(ssh_port), "%s@%s" % (ssh_user, host),
                    _shell_command(argv, cwd, env)]
    try:
        result = context.command(run_argv, timeout_seconds=timeout_seconds,
                                 input_text=step.get("input"), merge_stderr=True)
        returncode, output = result.returncode, result.stdout
    except TimeoutError as exc:
        returncode = 124
        output = ((getattr(exc, "partial_stdout", "") or "") +
                  "\n命令执行超时（%ss）" % timeout_seconds)
    execution = StepExecutionResult(returncode, output=output, command=argv)
    passed, actual, reason = evaluate_assertion(assertion, execution)
    _record_step(context, key, title, passed, actual,
                 format_psql_output(execution.output), reason, node)


def _sql_execution(context: CaseContext, step: dict[str, Any],
                   definition: dict[str, Any] | None):
    """Run one exported SQL step through psql with legacy arguments."""
    node = context.resolve_node(step.get("node", "primary"))
    assertion = step.get("assertion") or {}
    structured = assertion.get("type") in {
        "rows_equal", "rows_with_output_contains", "query_equals", "scalar_equals",
    }
    connection = step.get("connection") or (definition or {}).get("connection")
    if connection:
        connection = context.expand(connection)
    execution = execute_psql(
        context, node, step["sql"],
        user=step.get("user") or context.environment.get("user") or "postgres",
        database=step.get("database") or "postgres",
        structured=structured,
        timeout=step.get("command_timeout"),
        client_encoding=step.get("client_encoding"),
        password=step.get("password"),
        connection=connection)
    return node, execution


def _run_sql_step(context: CaseContext, step: dict[str, Any], index: int,
                  definition: dict[str, Any] | None) -> None:
    node, execution = _sql_execution(context, step, definition)
    passed, actual, reason = evaluate_assertion(step["assertion"], execution)
    _record_step(context, f"step-{index}", step.get("title") or f"step {index}",
                 passed, actual,
                 format_psql_output(execution.display_output), reason, node)


def _run_wait_sql_step(context: CaseContext, step: dict[str, Any], index: int,
                       definition: dict[str, Any] | None) -> None:
    key = f"step-{index}"
    title = step.get("title") or f"step {index}"
    deadline = time.time() + float(step.get("timeout", 30))
    last = None
    while time.time() < deadline:
        context.check_cancel()
        node, execution = _sql_execution(context, step, definition)
        passed, actual, reason = evaluate_assertion(step["assertion"], execution)
        last = (node, execution, actual, reason)
        if passed:
            _record_step(context, key, title, True, actual,
                         format_psql_output(execution.display_output), "", node)
            return
        time.sleep(float(step.get("interval", 1)))
    if last is None:
        _record_step(context, key, title, False, "未执行", "<无输出>", "等待超时")
        return
    node, execution, actual, reason = last
    reason = "等待 %ss 超时；%s" % (step.get("timeout", 30), reason)
    _record_step(context, key, title, False, actual,
                 format_psql_output(execution.display_output), reason, node)


def _run_background_sql_step(context: CaseContext, step: dict[str, Any],
                             index: int) -> None:
    key = f"step-{index}"
    title = step.get("title") or f"step {index}"
    node = context.resolve_node(step.get("node", "primary"))
    endpoint = context.node_endpoint(node)
    name = step["key"]
    running = context.values.setdefault("background_sql", {})
    if name in running:
        raise ValueError("后台 SQL key 已存在: %s" % name)
    script = "%s; SELECT pg_sleep(%s); %s" % (
        step["sql"].rstrip(";"), step["hold_seconds"],
        step["finish_sql"].rstrip(";"))
    argv = [
        _fbase_binary(context, "psql"), "-X", "-v", "ON_ERROR_STOP=1",
        "-v", "VERBOSITY=verbose", "-P", "pager=off",
        "-h", str(endpoint["host"]), "-p", str(endpoint["port"]),
        "-U", step.get("user") or context.environment.get("user") or "postgres",
        "-d", step.get("database") or "postgres", "-c", script,
    ]
    process = context.start_command(argv)
    running[name] = {"process": process, "command": argv, "node": node}

    def cleanup():
        entry = running.pop(name, None)
        if entry and entry["process"].poll() is None:
            entry["process"].terminate()
            context.finish_command(entry["command"], entry["process"], 5)

    context.defer_cleanup(cleanup, priority=300)
    time.sleep(float(step.get("settle_seconds", 1)))
    if process.poll() is not None:
        completed = context.finish_command(argv, process, 1)
        execution = StepExecutionResult(completed.returncode,
                                        output=completed.stdout, command=argv)
    else:
        execution = StepExecutionResult(
            0, output="后台 psql 已启动，pid=%s；事务保持中" % process.pid,
            command=argv)
    passed, actual, reason = evaluate_assertion(step["assertion"], execution)
    _record_step(context, key, title, passed, actual,
                 format_psql_output(execution.output), reason, node)


def _run_wait_background_sql_step(context: CaseContext, step: dict[str, Any],
                                  index: int) -> None:
    key = f"step-{index}"
    title = step.get("title") or f"step {index}"
    running = context.values.setdefault("background_sql", {})
    entry = running.pop(step["key"], None)
    if not entry:
        raise ValueError("未找到后台 SQL key: %s" % step["key"])
    completed = context.finish_command(
        entry["command"], entry["process"], step.get("timeout", 30))
    execution = StepExecutionResult(completed.returncode,
                                    output=completed.stdout,
                                    command=entry["command"])
    passed, actual, reason = evaluate_assertion(step["assertion"], execution)
    _record_step(context, key, title, passed, actual,
                 format_psql_output(execution.output), reason, entry["node"])


def _pg_ctl_argv(context: CaseContext, endpoint: dict[str, Any],
                 action: str) -> list[str]:
    """Build the legacy pg_ctl argv for one managed node action."""
    data_dir = endpoint.get("data_dir")
    if not data_dir:
        raise ValueError("节点缺少 data_dir，无法执行 pg_ctl")
    argv = [_fbase_binary(context, "pg_ctl"), "-D", str(data_dir)]
    if action == "start":
        argv += ["-l", str(Path(str(data_dir)) / "startup.log"), "-w", "start"]
    elif action == "stop":
        argv += ["-w", "-m", "fast", "stop"]
    elif action == "stop_immediate":
        argv += ["-w", "-m", "immediate", "stop"]
    elif action == "restart":
        argv += ["-l", str(Path(str(data_dir)) / "startup.log"), "-w", "restart"]
    elif action == "reload":
        argv += ["reload"]
    elif action == "status":
        argv += ["status"]
    else:
        raise ValueError("unknown pg_ctl action: %s" % action)
    return argv


def _run_pg_ctl(context: CaseContext, node: str, action: str,
                check: bool = True):
    """Run pg_ctl for one managed node like the legacy environment manager."""
    endpoint = context.node_endpoint(node)
    argv = _pg_ctl_argv(context, endpoint, action)
    try:
        result = context.command(argv, merge_stderr=True)
        returncode, output = result.returncode, result.stdout
    except TimeoutError as exc:
        returncode = 124
        output = getattr(exc, "partial_stdout", "") or ""
    if check and returncode != 0:
        raise RuntimeError("命令执行失败(%s): %s\n%s" % (
            returncode, " ".join(argv), output.rstrip()))
    return returncode, output


def _managed_node_order(context: CaseContext) -> list[str]:
    """Physical node keys in environment order for cluster-wide actions."""
    order = context.environment.get("node_order")
    if order:
        return [name for name in order if name in (context.environment.get("nodes") or {})]
    return list(context.environment.get("nodes") or {})


def _run_cluster_action(context: CaseContext, step: dict[str, Any],
                        index: int) -> None:
    """Apply start/stop/restart/reload across every managed node in order."""
    key = f"step-{index}"
    title = step.get("title") or f"step {index}"
    action = step.get("action")
    if action not in ("reload", "restart", "start", "stop"):
        raise ValueError("不支持的 cluster action: %s" % action)
    order = _managed_node_order(context)
    if action == "stop":
        order = list(reversed(order))
    if action == "reload":
        if not any(
                _run_pg_ctl(context, node, "status", check=False)[0] == 0
                for node in order):
            raise ValueError(
                "cluster %s 未运行，不能 reload"
                % context.environment.get("cluster") or context.environment.get("cluster_name"))
        for node in order:
            _run_pg_ctl(context, node, "reload")
    elif action == "start":
        for node in order:
            if _run_pg_ctl(context, node, "status", check=False)[0] != 0:
                _run_pg_ctl(context, node, "start")
    elif action == "stop":
        for node in order:
            if _run_pg_ctl(context, node, "status", check=False)[0] == 0:
                _run_pg_ctl(context, node, "stop")
    elif action == "restart":
        for node in order:
            status = _run_pg_ctl(context, node, "status", check=False)[0]
            _run_pg_ctl(context, node, "restart" if status == 0 else "start")
    execution = StepExecutionResult(0, output="%s complete" % action)
    passed, actual, reason = evaluate_assertion(step["assertion"], execution)
    _record_step(context, key, title, passed, actual, execution.output, reason)


def _run_node_action(context: CaseContext, step: dict[str, Any],
                     index: int) -> None:
    key = f"step-{index}"
    title = step.get("title") or f"step {index}"
    action = step.get("action")
    if action not in ("start", "stop", "stop_immediate", "restart"):
        raise ValueError("不支持的 node action: %s" % action)
    node = context.resolve_node(step["node"])
    _run_pg_ctl(context, node, action)
    execution = StepExecutionResult(0, output="%s %s complete" % (action, node))
    passed, actual, reason = evaluate_assertion(step["assertion"], execution)
    _record_step(context, key, title, passed, actual, execution.output, reason, node)


def _run_system_time_shift(context: CaseContext, step: dict[str, Any],
                           index: int) -> None:
    key = f"step-{index}"
    title = step.get("title") or f"step {index}"
    state = context.values.get("system_clock")
    if not state:
        raise ValueError("system_time_shift 需要 system_clock fixture")
    seconds = int(step["seconds"])
    if seconds <= 0:
        raise ValueError("system_time_shift seconds 必须大于 0")
    target = int(state["epoch"]) + seconds
    ntp = context.command(["sudo", "-n", "timedatectl", "set-ntp", "false"],
                          merge_stderr=True)
    if ntp.returncode != 0:
        raise RuntimeError("命令执行失败(%s): %s\n%s" % (
            ntp.returncode, "sudo -n timedatectl set-ntp false",
            ntp.stdout.rstrip()))
    try:
        process = context.command(
            ["sudo", "-n", "date", "-s", "@%s" % target], merge_stderr=True)
        returncode, output = process.returncode, process.stdout
    except TimeoutError as exc:
        returncode = 124
        output = getattr(exc, "partial_stdout", "") or ""
    if returncode == 0:
        state["changed"] = True
    argv = ["sudo", "-n", "date", "-s", "@%s" % target]
    execution = StepExecutionResult(
        returncode,
        output="原始 epoch=%s，目标 epoch=%s\n%s" % (state["epoch"], target, output),
        command=argv)
    passed, actual, reason = evaluate_assertion(step["assertion"], execution)
    _record_step(context, key, title, passed, actual,
                 format_psql_output(execution.output), reason)


def run_declared_step(context: CaseContext, step: dict[str, Any], index: int,
                      before_command: Callable[[list[str]], None] | None = None,
                      definition: dict[str, Any] | None = None) -> None:
    """Expand and dispatch one exported step to its platform runner."""
    step = context.expand(step)
    kind = step.get("type", "sql")
    if kind == "command":
        run_command_step(context, step, index, before_command)
        return
    if kind == "sql":
        _run_sql_step(context, step, index, definition)
        return
    if kind == "wait_sql":
        _run_wait_sql_step(context, step, index, definition)
        return
    if kind == "background_sql":
        _run_background_sql_step(context, step, index)
        return
    if kind == "wait_background_sql":
        _run_wait_background_sql_step(context, step, index)
        return
    if kind == "cluster_action":
        _run_cluster_action(context, step, index)
        return
    if kind == "node_action":
        _run_node_action(context, step, index)
        return
    if kind == "system_time_shift":
        _run_system_time_shift(context, step, index)
        return
    raise ValueError("平台暂不支持步骤类型: %s" % kind)


def run_declared_steps(context: CaseContext, steps: list[dict[str, Any]],
                       before_command: Callable[[list[str]], None] | None = None,
                       definition: dict[str, Any] | None = None) -> None:
    """Run exported steps with the legacy halt/continue contract.

    A failed step halts the case unless it declares ``continue_on_failure``;
    steps skipped by a halt are recorded BLOCKED.  Executor errors inside a
    step — including unresolvable node selectors — are recorded as step
    failures like the legacy runner; only cancellation propagates.
    """
    failures = []
    halted = False
    for index, step in enumerate(steps, 1):
        title = step.get("title") or f"step {index}"
        if halted:
            context.step(f"step-{index}", title, status="BLOCKED", details={
                "reason": "前序步骤失败，当前步骤不再具备有效前置条件",
            })
            continue
        try:
            run_declared_step(context, step, index, before_command,
                              definition=definition)
        except AssertionError as exc:
            failures.append("第 %s 步 %s: %s" % (index, title, exc))
            halted = not step.get("continue_on_failure", False)
        except Cancelled:
            raise
        except Exception as exc:  # noqa: BLE001 - executor errors are step failures
            context.step(f"step-{index}", title, status="FAIL",
                         details={"executor_error": str(exc)})
            failures.append("第 %s 步 %s: 执行器异常: %s" % (index, title, exc))
            halted = not step.get("continue_on_failure", False)
    if failures:
        raise AssertionError("; ".join(failures))
