"""psql command construction."""

from pathlib import Path


def build_psql_command(
    postgres_dir,
    host,
    port,
    user,
    database,
    sql,
    footer=None,
    output_format=None,
    field_separator=None,
    tuples_only=False,
    expanded=False,
):
    """Build a psql argv list without executing it."""
    command = [
        str(Path(postgres_dir) / "bin" / "psql"),
        "-h", str(host),
        "-p", str(port),
        "-U", str(user),
        "-d", str(database),
    ]
    if footer is not None:
        command.extend(["-P", "footer=%s" % ("on" if footer else "off")])
    if output_format is not None:
        command.extend(["-P", "format=%s" % output_format])
    if field_separator is not None:
        command.extend(["-F", str(field_separator)])
    if tuples_only:
        command.append("-t")
    if expanded:
        command.append("-x")
    command.extend(["-c", str(sql)])
    return command


def parse_psql_table(output):
    """
    Parse standard or expanded psql table output into a list of dicts.
    Returns [] if output does not contain a structured table.
    """
    if not output or not output.strip():
        return []

    lines = [line.rstrip() for line in output.strip().splitlines() if line.strip()]
    if not lines:
        return []

    # 1. Check for expanded output (-x format: -[ RECORD 1 ]...)
    if any(line.startswith("-[ RECORD ") for line in lines):
        records = []
        current = {}
        for line in lines:
            if line.startswith("-[ RECORD "):
                if current:
                    records.append(current)
                    current = {}
                continue
            if "|" in line:
                key, _, val = line.partition("|")
                current[key.strip()] = val.strip()
        if current:
            records.append(current)
        return records

    # 2. Check for standard table output (header + separator + data rows)
    sep_idx = -1
    for idx, line in enumerate(lines):
        # A separator line consists of dashes and pluses (e.g. "----+----+----")
        stripped = line.strip().replace(" ", "")
        if stripped and all(ch in "-+" for ch in stripped) and "-" in stripped:
            sep_idx = idx
            break

    if sep_idx > 0:
        header_line = lines[sep_idx - 1]
        headers = [col.strip() for col in header_line.split("|")]
        records = []
        for line in lines[sep_idx + 1:]:
            # Stop at summary like "(4 rows)"
            if line.strip().startswith("(") and "row" in line:
                break
            # Skip another separator if present
            stripped = line.strip().replace(" ", "")
            if stripped and all(ch in "-+" for ch in stripped):
                continue
            cols = [col.strip() for col in line.split("|")]
            if len(cols) == len(headers):
                records.append({headers[i]: cols[i] for i in range(len(headers))})
        return records

    # 3. Check for tab or pipe separated unaligned output
    if lines:
        delim = "\t" if "\t" in lines[0] else ("|" if "|" in lines[0] else None)
        if delim:
            headers = [col.strip() for col in lines[0].split(delim)]
            records = []
            for line in lines[1:]:
                if line.strip().startswith("(") and "row" in line:
                    break
                cols = [col.strip() for col in line.split(delim)]
                if len(cols) == len(headers):
                    records.append({headers[i]: cols[i] for i in range(len(headers))})
            return records

    return []


def assert_table_rows(output, expected_rows, key="node_name"):
    """
    Assert that table output contains expected rows and fields.
    
    expected_rows: dict mapping key_value -> dict of {column: expected_value}
    e.g. {
        "pg_220": {"group_role": "write-leader", "state": "active", "is_abnormal": "OK"},
        "pg_240": {"group_role": "replica", "primary": "pg_220", "state": "active"},
    }
    
    Returns: (passed: bool, summary_text: str, details: list of dicts)
    """
    rows = parse_psql_table(output)
    row_map = {row.get(key): row for row in rows if key in row}
    
    all_passed = True
    summary_lines = []
    details = []

    for key_val, expected_fields in expected_rows.items():
        actual_row = row_map.get(key_val)
        if actual_row is None:
            all_passed = False
            summary_lines.append("❌ [%s=%s] 未在控制台输出中找到对应行" % (key, key_val))
            details.append({"key": key_val, "status": "MISSING", "errors": ["Row not found"]})
            continue

        row_errors = []
        field_matches = []
        for field, expected_val in expected_fields.items():
            actual_val = actual_row.get(field)
            if actual_val != expected_val:
                row_errors.append("%s: 期望='%s', 实际='%s'" % (field, expected_val, actual_val))
            else:
                field_matches.append("%s='%s'" % (field, actual_val))

        if row_errors:
            all_passed = False
            summary_lines.append("❌ [%s=%s] 字段不匹配: %s" % (key, key_val, "; ".join(row_errors)))
            details.append({"key": key_val, "status": "MISMATCH", "errors": row_errors, "matches": field_matches})
        else:
            summary_lines.append("✅ [%s=%s] 全部字段匹配: %s" % (key, key_val, ", ".join(field_matches)))
            details.append({"key": key_val, "status": "PASS", "matches": field_matches})

    return all_passed, "\n".join(summary_lines), details
