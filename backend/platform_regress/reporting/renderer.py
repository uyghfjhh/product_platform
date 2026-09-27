"""Renderer for the shared report.txt contract."""


def _append_value(lines, prefix, value):
    rendered = str(value) if value is not None else "<empty>"
    value_lines = rendered.splitlines() or ["<empty>"]
    if len(value_lines) == 1:
        lines.append("%s%s" % (prefix, value_lines[0]))
        return
    lines.append(prefix.rstrip())
    for raw_line in value_lines:
        lines.append("      %s" % raw_line)


def render_psql_table_from_pipe_text(output):
    """Render the framework's machine-readable psql output as a psql table."""
    rows = []
    for raw_line in str(output).splitlines():
        line = raw_line.rstrip()
        if not line:
            continue
        if "|" not in line:
            return str(output).rstrip("\n") or "<empty>"
        rows.append([cell.strip() for cell in line.split("|")])
    if not rows:
        return "<empty>"
    widths = [max(len(row[index]) if index < len(row) else 0 for row in rows)
              for index in range(max(len(row) for row in rows))]

    def render_row(row):
        return " | ".join(
            (row[index] if index < len(row) else "").ljust(widths[index])
            for index in range(len(widths))
        )

    rendered = [render_row(rows[0]), "-+-".join("-" * width for width in widths)]
    rendered.extend(render_row(row) for row in rows[1:])
    count = len(rows) - 1
    rendered.append("(%d %s)" % (count, "row" if count == 1 else "rows"))
    return "\n".join(rendered)


def render_report(document):
    """Render a structured document without suite-specific wording."""
    lines = [
        "用例: %s" % document.target,
        "结论: %s" % document.status,
        "测试开始时间: %s" % document.started_at,
        "测试结束时间: %s" % document.finished_at,
    ]
    if document.status == "PASS" and document.pass_reason:
        lines.append("通过原因: %s" % document.pass_reason)
    elif document.status == "FAIL" and document.failure_reason:
        lines.append("失败原因: %s" % document.failure_reason)

    lines.extend(["", "验证目的:", "  %s" % document.purpose])
    if document.coverage_items:
        lines.extend(["", "%s:" % document.coverage_title])
        lines.extend(
            "  %d. %s" % (index, item)
            for index, item in enumerate(document.coverage_items, 1)
        )
    if document.coverage_mapping:
        mapping_text = "\n".join([
            "报告步骤|对应%s|本步骤检查" % document.coverage_title,
        ] + ["%s|%s|%s" % item for item in document.coverage_mapping])
        lines.extend(["", "步骤与%s对应:" % document.coverage_title])
        _append_value(lines, "  ", render_psql_table_from_pipe_text(mapping_text))
    if document.config_lines:
        lines.extend(["", "关键配置:"])
        lines.extend("  %s" % item for item in document.config_lines)
    if document.overview_steps:
        lines.extend(["", "测试步骤概览:"])
        lines.extend(
            "  %d. %s" % (index, item)
            for index, item in enumerate(document.overview_steps, 1)
        )
    if document.steps:
        lines.extend(["", "验证步骤:"])

    check_number = 0
    for step_number, step in enumerate(document.steps, 1):
        lines.append("步骤 %d: %s" % (step_number, step.title))
        if step.coverage:
            _append_value(lines, "    对应%s: " % document.coverage_title, step.coverage)
        if step.coverage_check:
            _append_value(lines, "    本步骤检查: ", step.coverage_check)
        for item in step.execution:
            _append_value(lines, "    %s: " % item.get("label", "实际执行"), item.get("text", ""))
        for item in step.intermediate:
            _append_value(lines, "    %s: " % item.get("label", "中间状态"), item.get("text", ""))
        for item in step.evidence:
            _append_value(lines, "    %s: " % item.get("label", "证据"), item.get("text", ""))
        for label, value in step.details:
            _append_value(lines, "    %s: " % label, value)
        if step.key_expected is not None:
            _append_value(lines, "    关键期望: ", step.key_expected)
        if step.expected is not None:
            _append_value(lines, "    预期: ", step.expected)
        if step.actual is not None:
            _append_value(lines, "    实际: ", step.actual)
        if step.result is not None:
            lines.append("    判定: %s" % step.result)
        for check in step.checks:
            check_number += 1
            lines.extend(["", "检测项 %d: %s" % (check_number, check.title)])
            _append_value(lines, "    预期: ", check.expected)
            _append_value(lines, "    实际: ", check.actual)
            lines.append("    判定: %s" % check.result)
    return "\n".join(lines) + "\n"
