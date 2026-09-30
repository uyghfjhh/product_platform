"""Shared step primitives: result model, psql output parsing, assertion
evaluation and the step-record contract used by every step family."""

from __future__ import annotations

import csv
import io
import re
import shlex
import unicodedata
from dataclasses import dataclass, field
from typing import Any

from ..engine import CaseContext


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
# NULL sentinel token written into CSV evidence; the value is part of the
# on-disk protocol and must stay byte-identical with legacy reports.
_NULL_TOKEN = "__FBASE_REGRESS_NULL__"
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
        rows.append([None if value == _NULL_TOKEN else value for value in record])
    return columns, rows, "\n".join(diagnostics)


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


def step_user(context: CaseContext, step: dict[str, Any]) -> str | None:
    """Resolve a step's ``user``; ``{env.user}`` names the environment user."""
    user = step.get("user")
    if user == "{env.user}":
        return context.environment.get("user") or "postgres"
    return user


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
