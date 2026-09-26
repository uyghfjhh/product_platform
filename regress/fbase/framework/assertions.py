import re

from framework.models import ExecutionResult


_SQLSTATE = re.compile(r"ERROR:\s+([0-9A-Z]{5}):\s+([^\n]+)")
_ASSERTIONS = {}


def register_assertion(name):
    def register(callback):
        _ASSERTIONS[name] = callback
        return callback
    return register


def assertion_names():
    return set(_ASSERTIONS)


def assertion(name, **options):
    result = {"type": name}
    result.update(options)
    return result


def rows_equal(rows):
    return assertion("rows_equal", rows=rows)


def rows_with_output_contains(rows, value):
    return assertion("rows_with_output_contains", rows=rows, value=value)


def scalar_equals(value):
    return assertion("scalar_equals", value=value)


def sql_error(sqlstate, message_contains):
    return assertion("sql_error", sqlstate=sqlstate,
                     message_contains=message_contains)


def sql_fails(message_contains):
    return assertion("sql_fails", message_contains=message_contains)


def output_contains(*values):
    return assertion("output_contains", values=list(values))


def output_contains_text(*values):
    return assertion("output_contains_text", values=list(values))


def command_succeeds():
    return assertion("command_succeeds")


def command_fails(message_contains):
    return assertion("command_fails", message_contains=message_contains)


def meaningful_lines(output):
    result = []
    for line in (output or "").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("Password will expire at "):
            continue
        result.append(stripped)
    return result


def format_psql_output(output):
    lines = []
    for line in (output or "").splitlines():
        if line.strip().startswith("Password will expire at "):
            continue
        lines.append(line.rstrip())
    return "\n".join(lines).strip("\n") or "<空>"


def _legacy_aligned_rows(output):
    lines = format_psql_output(output).splitlines()
    data = [line for line in lines if "|" in line]
    if len(data) < 2:
        return []
    return [[value.strip() for value in data[-1].split("|")]]


def _execution_result(process):
    if isinstance(process, ExecutionResult):
        return process
    output = getattr(process, "stdout", "") or ""
    match = _SQLSTATE.search(output)
    return ExecutionResult(
        getattr(process, "returncode", 1), output=output,
        rows=_legacy_aligned_rows(output),
        sqlstate=match.group(1) if match else None,
        error_message=match.group(2).strip() if match else None,
    )


def evaluate(assertion_spec, process):
    result = _execution_result(process)
    kind = assertion_spec.get("type")
    callback = _ASSERTIONS.get(kind)
    if not callback:
        return False, "未知断言类型=%s" % kind, "框架不支持该断言类型"
    return callback(assertion_spec, result)


def evaluate_step(step, process):
    return evaluate(step["assertion"], process)


@register_assertion("rows_equal")
def _rows_equal(spec, result):
    expected = spec["rows"]
    actual_rows = result.rows
    passed = result.returncode == 0 and actual_rows == expected
    def rows_text(rows):
        return "; ".join("|".join("" if value is None else str(value) for value in row)
                         for row in rows) or "<空>"
    actual = "返回行=%s，退出码=%s" % (rows_text(actual_rows), result.returncode)
    reason = "" if passed else "预期返回行=%s；实际%s" % (rows_text(expected), actual)
    return passed, actual, reason


@register_assertion("rows_with_output_contains")
def _rows_with_output_contains(spec, result):
    expected = spec["rows"]
    marker = spec["value"]
    passed = (result.returncode == 0 and result.rows == expected and
              marker in (result.output or ""))
    rows_text = "; ".join("|".join("" if value is None else str(value) for value in row)
                          for row in result.rows) or "<空>"
    actual = "返回行=%s，输出包含=%s，退出码=%s" % (
        rows_text, marker if marker in (result.output or "") else "<未匹配>", result.returncode)
    reason = "" if passed else "预期返回行=%s 且输出包含 %s；实际%s" % (
        expected, marker, actual)
    return passed, actual, reason


@register_assertion("query_equals")
def _query_equals(spec, result):
    actual_value = "|".join("" if value is None else str(value)
                            for value in result.rows[-1]) if result.rows else ""
    passed = result.returncode == 0 and actual_value == spec["value"]
    actual = "返回值=%s，退出码=%s" % (actual_value or "<空>", result.returncode)
    reason = "" if passed else "预期返回值=%s；实际%s" % (spec["value"], actual)
    return passed, actual, reason


@register_assertion("scalar_equals")
def _scalar_equals(spec, result):
    actual_value = result.rows[0][0] if len(result.rows) == 1 and len(result.rows[0]) == 1 else None
    passed = result.returncode == 0 and actual_value == spec["value"]
    actual = "返回值=%s，退出码=%s" % (actual_value, result.returncode)
    reason = "" if passed else "预期返回值=%s；实际%s" % (spec["value"], actual)
    return passed, actual, reason


@register_assertion("sql_error")
def _sql_error(spec, result):
    sqlstate = result.sqlstate or "unknown"
    message = result.error_message or "未捕获 ERROR"
    passed = (result.returncode != 0 and sqlstate == spec["sqlstate"] and
              spec["message_contains"] in message)
    actual = "SQLSTATE=%s，错误=%s，退出码=%s" % (
        sqlstate, message, result.returncode)
    reason = "" if passed else (
        "预期 SQLSTATE=%s 且错误包含 %s；实际%s" %
        (spec["sqlstate"], spec["message_contains"], actual)
    )
    return passed, actual, reason


@register_assertion("sql_fails")
def _sql_fails(spec, result):
    sqlstate = result.sqlstate or "unknown"
    message = result.error_message or "未捕获 ERROR"
    passed = (result.returncode != 0 and
              spec["message_contains"] in message)
    actual = "SQLSTATE=%s，错误=%s，退出码=%s" % (
        sqlstate, message, result.returncode)
    reason = "" if passed else (
        "预期 SQL 执行失败且错误包含 %s；实际%s" %
        (spec["message_contains"], actual)
    )
    return passed, actual, reason


@register_assertion("output_contains")
def _output_contains(spec, result):
    lines = meaningful_lines(result.output)
    missing = [value for value in spec["values"] if value not in lines]
    passed = result.returncode == 0 and not missing
    actual = "输出=%s，退出码=%s" % (" | ".join(lines) or "<空>", result.returncode)
    reason = "" if passed else "预期输出包含%s；实际%s" % (spec["values"], actual)
    return passed, actual, reason


@register_assertion("output_contains_text")
def _output_contains_text(spec, result):
    output = result.output or ""
    missing = [value for value in spec["values"] if value not in output]
    passed = result.returncode == 0 and not missing
    actual = "输出=%s，退出码=%s" % (format_psql_output(output), result.returncode)
    reason = "" if passed else "预期输出包含%s；实际%s" % (spec["values"], actual)
    return passed, actual, reason


@register_assertion("command_succeeds")
def _command_succeeds(unused_spec, result):
    passed = result.returncode == 0
    actual = "退出码=%s，输出=%s" % (
        result.returncode, format_psql_output(result.output))
    reason = "" if passed else "预期退出码=0；实际%s" % actual
    return passed, actual, reason


@register_assertion("command_fails")
def _command_fails(spec, result):
    output = format_psql_output(result.output)
    passed = result.returncode != 0 and spec["message_contains"] in output
    actual = "退出码=%s，输出=%s" % (result.returncode, output)
    reason = "" if passed else "预期退出码非 0 且输出包含 %s；实际%s" % (
        spec["message_contains"], actual)
    return passed, actual, reason
