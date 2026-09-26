"""fbasecman console output parsing."""


class ConsoleQueryError(RuntimeError):
    pass


def parse_pipe_rows(output):
    rows = []
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("psql:"):
            raise ConsoleQueryError("console query failed: %s" % line)
        rows.append(line.split("|"))
    return rows
