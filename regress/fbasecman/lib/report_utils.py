def render_record_from_pipe_row(row_text, headers):
    if not row_text:
        return "<empty>"
    cells = [cell.strip() for cell in row_text.split("|")]
    label_width = max(len(str(col)) for col in headers) if headers else 0
    lines = ["-[ RECORD 1 ]--------"]
    for idx, header in enumerate(headers):
        value = cells[idx] if idx < len(cells) else ""
        lines.append("%s | %s" % (str(header).ljust(label_width), value))
    return "\n".join(lines)


def render_psql_expanded_from_pipe_text(output):
    """Render the complete result of a console psql command for report evidence."""
    rows = []
    for raw_line in output.splitlines():
        line = raw_line.rstrip()
        if not line:
            continue
        if "|" not in line:
            return output.rstrip("\n")
        rows.append([cell.strip() for cell in line.split("|")])
    if not rows:
        return "<empty>"
    headers = rows[0]
    records = rows[1:]
    if not records:
        return "(0 rows)"
    label_width = max(len(str(col)) for col in headers)
    rendered = []
    for idx, row in enumerate(records, 1):
        rendered.append("-[ RECORD %d ]%s" % (idx, "-" * 8))
        for col_idx, header in enumerate(headers):
            value = row[col_idx] if col_idx < len(row) else ""
            rendered.append("%s | %s" % (str(header).ljust(label_width), value))
    return "\n".join(rendered)
