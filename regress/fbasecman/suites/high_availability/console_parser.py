"""Structured console output parser and semantic assertion library for fbasecman."""

import re


class ConsoleAssertionError(AssertionError):
    """Raised when a console field does not match the expected business value."""
    pass


def parse_console_pipe_table(output):
    """Parse psql pipe output into a list of dicts using the first row as headers.

    Expected format:
        header1 | header2 | header3
        val1    | val2    | val3
    """
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    if not lines:
        return []

    # Find the header line (first line containing '|' that doesn't start with psql:)
    header_index = -1
    for idx, line in enumerate(lines):
        if line.startswith("psql:"):
            continue
        if "|" in line:
            header_index = idx
            break

    if header_index == -1:
        return []

    headers = [col.strip() for col in lines[header_index].split("|")]
    records = []

    for line in lines[header_index + 1:]:
        if line.startswith("psql:") or line.startswith("(") and "rows)" in line:
            continue
        if "|" not in line:
            continue
        values = [col.strip() for col in line.split("|")]
        # Match values to headers
        row_dict = {}
        for h_idx, header in enumerate(headers):
            row_dict[header] = values[h_idx] if h_idx < len(values) else ""
        records.append(row_dict)

    return records


class ConsoleSnapshot(object):
    """Structured view of console SHOW outputs with semantic assertion methods."""

    def __init__(self, raw_output):
        self.raw_output = raw_output
        self.records = parse_console_pipe_table(raw_output)

    def find_rows(self, **filters):
        """Find all rows matching given column key-value filters."""
        matched = []
        for row in self.records:
            match = True
            for key, expected_val in filters.items():
                if expected_val is None:
                    continue
                actual_val = row.get(key, "")
                if str(actual_val) != str(expected_val):
                    match = False
                    break
            if match:
                matched.append(row)
        return matched

    def find_one(self, **filters):
        """Find exactly one row matching filters or raise ConsoleAssertionError."""
        rows = self.find_rows(**filters)
        if not rows:
            raise ConsoleAssertionError(
                "No console row matched filters %r; available rows:\n%s"
                % (filters, self.render())
            )
        return rows[0]

    def assert_field(self, filters, field_name, expected_value):
        """Assert that a specific field in the matched row equals expected_value."""
        row = self.find_one(**filters)
        actual = row.get(field_name, "")
        if str(actual) != str(expected_value):
            raise ConsoleAssertionError(
                "Field %r mismatch for filter %r: expected %r, got %r\nFull row: %r"
                % (field_name, filters, expected_value, actual, row)
            )
        return row

    def assert_candidate_absent(self, candidate_node):
        """Assert that candidate_node is absent from all rows in this snapshot."""
        for row in self.records:
            if row.get("candidate_node") == candidate_node:
                raise ConsoleAssertionError(
                    "Expected candidate %r to be ABSENT, but found in row:\n%r"
                    % (candidate_node, row)
                )

    def assert_candidate_present(self, candidate_node, candidate_type=None):
        """Assert that candidate_node is present in this snapshot."""
        filters = {"candidate_node": candidate_node}
        if candidate_type:
            filters["candidate_type"] = candidate_type
        return self.find_one(**filters)

    def assert_write_target_count(self, expected_count=1, user_name=None):
        """Assert that exactly expected_count rows have is_write_target='true'."""
        write_targets = [
            r for r in self.records
            if r.get("is_write_target") == "true" and (user_name is None or r.get("user_name") == user_name)
        ]
        if len(write_targets) != expected_count:
            raise ConsoleAssertionError(
                "Expected exactly %d is_write_target='true' rows (user=%r), found %d:\n%r"
                % (expected_count, user_name, len(write_targets), write_targets)
            )
        return write_targets

    def render(self):
        """Render parsed rows as readable text."""
        if not self.records:
            return "<empty table>"
        headers = list(self.records[0].keys())
        lines = [" | ".join(headers)]
        for row in self.records:
            lines.append(" | ".join(str(row.get(h, "")) for h in headers))
        return "\n".join(lines)

    def format_table(self):
        """Render raw output as formatted psql table with SQL prefix."""
        from framework.reporting.renderer import render_psql_table_from_pipe_text
        tbl = render_psql_table_from_pipe_text(self.raw_output)
        if hasattr(self, "sql") and self.sql:
            return "%s\n%s" % (self.sql.strip(), tbl)
        return tbl

    def format_record(self, title=None, **filters):
        """Render a single record as -[ RECORD 1 ]- format with SQL prefix."""
        if filters:
            row = self.find_one(**filters)
        elif self.records:
            row = self.records[0]
        else:
            return "<empty>"
        label_width = max(len(str(k)) for k in row.keys())
        lines = []
        if title:
            lines.append(title)
        elif hasattr(self, "sql") and self.sql:
            lines.append(self.sql.strip())
        lines.append("-[ RECORD 1 ]--------")
        for k, v in row.items():
            lines.append("%s | %s" % (str(k).ljust(label_width), v))
        return "\n".join(lines)

