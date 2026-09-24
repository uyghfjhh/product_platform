"""Source-backed descriptions of JDBC business actions for test reports."""

import re


def without_phase_markers(output):
    """Keep internal driver coordination markers out of business reports."""
    return "\n".join(
        line for line in str(output).splitlines()
        if not re.match(r"^(?:PHASE|READY)[A-Z0-9_=:-]*", line.strip())
    ).strip()


def jdbc_api_calls(source):
    """Return the JDBC APIs actually present in a checked-in Java driver.

    This describes source-level client actions.  It intentionally does not
    infer PostgreSQL wire packets from Java calls.
    """
    text = source.read_text(encoding="utf-8", errors="replace")
    calls = [
        "%s.%s()" % pair for pair in re.findall(
            r"\b([A-Za-z_][A-Za-z0-9_]*)\.(prepareStatement|createStatement|"
            r"executeQuery|executeUpdate|execute|set[A-Za-z0-9_]+|getResultSet|"
            r"getMoreResults|getUpdateCount|next|close|commit|rollback|setAutoCommit|"
            r"setReadOnly)\s*\(",
            text,
        )
    ]
    ordered = []
    for call in calls:
        if call not in ordered:
            ordered.append(call)
    return ordered


def jdbc_prepared_operations(source):
    """Extract PreparedStatement SQL with bindings from its own Java scope.

    Binding values are intentionally associated with the statement variable that
    receives them.  A file can contain many PreparedStatements; attaching every
    ``setInt`` in the file to every SQL made the report look plausible while
    being misleading.
    """
    text = source.read_text(encoding="utf-8", errors="replace")
    strings = {}
    for name, value in re.findall(
            r'\b(?:final\s+)?String\s+(\w+)\s*=\s*"((?:\\.|[^"\\])*)"', text):
        strings[name] = value.replace('\\"', '"').replace('\\n', '\n')
    declaration = re.compile(
        r'(?:[A-Za-z_][A-Za-z0-9_]*\.)*PreparedStatement\s+(?P<name>\w+)\s*=\s*'
        r'\w+\.prepareStatement\s*\(\s*'
        r'(?P<sql>"(?:\\.|[^"\\])*"|\w+)\s*\)'
    )

    def matching_body(start):
        # PreparedStatement declarations normally sit inside a try block whose
        # opening brace precedes the declaration.  Restrict bindings to that
        # block; scanning from the next brace would merge parameters from later
        # statements in the same method.
        closing = text.find("}", start)
        return text[start:closing if closing >= 0 else len(text)]

    operations = []
    for match in declaration.finditer(text):
        name = match.group("name")
        raw_sql = match.group("sql")
        if raw_sql.startswith('"'):
            sql = raw_sql[1:-1].replace('\\"', '"').replace('\\n', '\n')
        elif raw_sql not in strings:
            # Runtime SQL arguments are rendered from the Java program output;
            # do not invent a literal named ``sql`` in the report.
            continue
        else:
            sql = strings[raw_sql]
        body = matching_body(match.end())
        parameters = []
        for index, value in re.findall(
                r'\b%s\.set(?:Int|Long|String|Boolean|Object)\s*\(\s*(\d+)\s*,\s*([^\)]+)\)'
                % re.escape(name), body):
            rendered = "$%s=%s" % (index, value.strip())
            if rendered not in parameters:
                parameters.append(rendered)
        existing = next((item for item in operations if item["sql"] == sql), None)
        if existing is None:
            operations.append({"sql": sql, "parameters": parameters})
        else:
            for parameter in parameters:
                if parameter not in existing["parameters"]:
                    existing["parameters"].append(parameter)
    return operations


def render_jdbc_action(source, jdbc_url, output, operations=None):
    """Render the concrete JDBC action and its observed program output."""
    lines = ["连接参数:", jdbc_url, "", "JDBC 核心调用:"]
    calls = jdbc_api_calls(source)
    lines.extend(calls or ["<未从 Java 源码提取到 JDBC 调用>"])
    report_operations = jdbc_prepared_operations(source) if operations is None else operations
    if report_operations:
        lines.extend(["", "PreparedStatement SQL 和参数:"])
        for index, operation in enumerate(report_operations, 1):
            lines.append("%d. SQL: %s" % (index, operation["sql"]))
            lines.append("   参数: %s" % (", ".join(operation["parameters"]) or "无"))
    return "\n".join(lines), output.rstrip() or "<empty>"
