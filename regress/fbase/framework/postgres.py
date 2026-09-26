import csv
import io
import re
import unicodedata

from framework.errors import OperationError
from framework.models import ExecutionResult


_SQLSTATE = re.compile(r"ERROR:\s+([0-9A-Z]{5}):\s+([^\n]+)")
_NULL = "__FBASE_REGRESS_NULL__"
_GUC_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*$")
_PSQL_DIAGNOSTIC = re.compile(r"^(WARNING|NOTICE|INFO|DETAIL|HINT|CONTEXT|LOCATION):")


def _render_aligned(columns, rows):
    if not columns:
        return "<空>"
    text_rows = [["" if value is None else str(value) for value in row] for row in rows]
    def display_width(value):
        return sum(2 if unicodedata.east_asian_width(char) in ("W", "F") else 1
                   for char in value)

    widths = []
    for index, column in enumerate(columns):
        values = [str(column)] + [row[index] if index < len(row) else "" for row in text_rows]
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


def _parse_csv(output):
    """Parse psql CSV after preserving server NOTICE/WARNING diagnostics."""
    lines = [
        line for line in (output or "").splitlines()
        if not line.strip().startswith("Password will expire at ")]
    diagnostics = []
    # VERBOSITY=verbose emits a diagnostic header, indented continuation lines,
    # then LOCATION.  Keep that complete block out of CSV parsing but preserve
    # it in the rendered psql evidence.
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


class PostgresClient(object):
    def __init__(self, manager, command_runner, touch_node=None):
        self.manager = manager
        self.command_runner = command_runner
        self.touch_node = touch_node or (lambda unused: None)

    def execute(self, node_name, user, database, sql, structured=False, timeout=None,
                client_encoding=None, password=None, connection=None):
        node = self.manager.node(node_name)
        connection = connection or {}
        self.touch_node(node_name)
        command = [
            self.manager.binary("psql"), "-X", "-v", "ON_ERROR_STOP=1",
            "-v", "VERBOSITY=verbose", "-P", "pager=off",
            "-h", connection.get("host", node["host"]),
            "-p", str(connection.get("port", node["port"])),
            "-U", user, "-d", database,
        ]
        if structured:
            command.extend(["--csv", "-P", "null=%s" % _NULL])
        command.extend(["-c", sql])
        environment = []
        if client_encoding:
            environment.append("PGCLIENTENCODING=%s" % client_encoding)
        if password is not None:
            environment.append("PGPASSWORD=%s" % password)
        if environment:
            command = ["env"] + environment + command
        process = self.command_runner.run(command, check=False, timeout=timeout)
        output = process.stdout or ""
        match = _SQLSTATE.search(output)
        columns = []
        rows = []
        display = output
        if structured and process.returncode == 0:
            try:
                columns, rows, diagnostics = _parse_csv(output)
                aligned = _render_aligned(columns, rows)
                display = "%s\n%s" % (diagnostics, aligned) if diagnostics else aligned
            except (csv.Error, IndexError, ValueError):
                columns, rows = [], []
        return ExecutionResult(
            process.returncode,
            output=output,
            display_output=display,
            command=command,
            columns=columns,
            rows=rows,
            sqlstate=match.group(1) if match else None,
            error_message=match.group(2).strip() if match else None,
        )

    def scalar(self, node_name, database, sql, user="postgres"):
        result = self.execute(node_name, user, database, sql, structured=True)
        if result.returncode != 0:
            raise RuntimeError(result.output.strip() or "SQL 执行失败")
        if len(result.rows) != 1 or len(result.rows[0]) != 1:
            raise RuntimeError("预期单行单列，实际为 %s 行" % len(result.rows))
        return result.rows[0][0]

    def execute_checked(self, node_name, user, database, sql, structured=False):
        result = self.execute(node_name, user, database, sql, structured)
        if result.returncode != 0:
            raise OperationError(result.output.strip() or "SQL 执行失败")
        return result

    def inspect_setting_configuration(self, node_name, name):
        """Return the pg_file_settings entries that define one GUC."""
        if not _GUC_NAME.match(name):
            raise OperationError("非法 PostgreSQL 参数名: %s" % name)
        sql = (
            "SELECT sourcefile AS config_file, sourceline AS line, "
            "format('%%s = %%L', name, setting) AS configuration, "
            "applied, COALESCE(error, '') AS error "
            "FROM pg_file_settings WHERE name = '%s' ORDER BY seqno" % name
        )
        result = self.execute(
            node_name, "postgres", "postgres", sql, structured=True)
        return {
            "sql": sql,
            "output": result.display_output,
            "error": "" if result.returncode == 0 else
                     (result.output.strip() or "pg_file_settings 查询失败"),
        }

    def check_setting(self, node_name, spec):
        name = spec["name"]
        if not _GUC_NAME.match(name):
            raise OperationError("非法 PostgreSQL 参数名: %s" % name)
        configuration = self.inspect_setting_configuration(node_name, name)
        sql = "SHOW %s" % name
        result = self.execute(
            node_name, "postgres", "postgres", sql, structured=True)
        actual = (result.rows[0][0] if result.returncode == 0 and
                  len(result.rows) == 1 and len(result.rows[0]) == 1
                  else "<查询失败>")
        if "equals" in spec:
            expected = str(spec["equals"])
            requirement = "等于 %s" % expected
            matched = (str(actual).lower() == expected.lower())
        else:
            expected = str(spec["contains"])
            requirement = "包含 %s" % expected
            values = {item.strip() for item in str(actual).split(",")}
            matched = expected in values or expected in str(actual)
        matched = result.returncode == 0 and matched
        return {
            "source": spec.get("source", "requirement"),
            "node": node_name, "name": name, "sql": sql,
            "output": result.display_output, "actual": actual,
            "config_sql": configuration["sql"],
            "config_output": configuration["output"],
            "config_error": configuration["error"],
            "requirement": requirement, "purpose": spec["purpose"],
            "matched": matched,
            "error": "" if result.returncode == 0 else
                     (result.output.strip() or "SHOW 执行失败"),
        }
