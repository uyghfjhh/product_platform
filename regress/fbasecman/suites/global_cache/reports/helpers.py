"""Formatting shared by global-cache report domains."""

from lib.report_utils import render_record_from_pipe_row

GLOBAL_STATS_HEADERS = (
    "total_entries", "referenced_entries", "unreferenced_entries",
    "bypass_entries", "capacity", "hits", "misses", "evictions",
)


def stats_record_or_text(stats):
    if not stats:
        return "<未采集到控制台统计>"
    row = "|".join(str(stats.get(key, "")) for key in GLOBAL_STATS_HEADERS)
    return render_record_from_pipe_row(row, GLOBAL_STATS_HEADERS)


def check_actual(item):
    actual = record_or_text(item.get("actual", "<未提供实际观察>"))
    stats = item.get("console_stats")
    if stats:
        actual += "\n控制台统计:\n" + stats_record_or_text(stats)
    return actual


def record_or_text(value):
    if not value:
        return "<未采集到实际观察>"
    if isinstance(value, str) and "\n" in value:
        lines = value.splitlines()
        global_rows = [
            line.strip() for line in lines
            if line.strip().startswith("__fbasecman_") and line.count("|") == 4
        ]
        if global_rows:
            description = [
                line for line in lines
                if not (line.strip().startswith("__fbasecman_") and line.count("|") == 4)
            ]
            rendered = global_records_or_text(global_rows)
            return "\n".join(description + ["控制台条目:", rendered])
    if isinstance(value, str) and "\n" not in value and "|" in value:
        row = [part.strip() for part in value.split("|")]
        if len(row) == 5 and row[0].startswith("__fbasecman_"):
            return render_record_from_pipe_row(
                value,
                ["global_name", "description", "sql_class", "has_bypass_response", "ref_count"],
            )
        if len(row) == 6 and "=" not in row[0]:
            return render_record_from_pipe_row(
                value,
                ["type", "user", "database", "sid", "definition", "refcount"],
            )
    return value


def global_records_or_text(rows):
    if not rows:
        return "<未采集到 global entry>"
    return "\n".join(
        render_record_from_pipe_row(
            row,
            ["global_name", "description", "sql_class", "has_bypass_response", "ref_count"],
        ).replace("-[ RECORD 1 ]--------", "-[ RECORD %s ]--------" % index, 1)
        for index, row in enumerate(rows, 1)
    )


def jdbc_sequence_call(operation):
    sql = operation["sql"].replace("\\", "\\\\").replace('"', '\\"')
    mode = operation["mode"]
    if mode == "statement":
        return 'Statement st = conn.createStatement(); st.execute("%s");' % sql
    if mode.startswith("query_int:"):
        value = mode.split(":", 1)[1]
        return ('PreparedStatement ps = conn.prepareStatement("%s"); '
                'ps.setInt(1, %s); ResultSet rs = ps.executeQuery();') % (sql, value)
    if mode.startswith("execute_int:"):
        value = mode.split(":", 1)[1]
        return ('PreparedStatement ps = conn.prepareStatement("%s"); '
                'ps.setInt(1, %s); ps.execute();') % (sql, value)
    if mode.startswith("query_columns:"):
        return ('PreparedStatement ps = conn.prepareStatement("%s"); '
                'ResultSet rs = ps.executeQuery(); // 输出列: %s') % (
                    sql, mode.split(":", 1)[1]
                )
    if mode == "query":
        return 'PreparedStatement ps = conn.prepareStatement("%s"); ResultSet rs = ps.executeQuery();' % sql
    if mode == "execute":
        return 'PreparedStatement ps = conn.prepareStatement("%s"); ps.execute();' % sql
    return "<未知 JDBC 操作模式: %s>" % mode


def sequence_calls_text(sequence):
    return "\n".join(jdbc_sequence_call(item) for item in sequence.get("operations", []))


def describe_pg_log_window(pg_log_verify, sql):
    before = pg_log_verify.get("before_count", "")
    after = pg_log_verify.get("after_count", "")
    if pg_log_verify.get("delta") == 0:
        return "PG 日志观察: 验证窗口内未新增 %s 的记录（验证前后匹配记录数均为 %s）。" % (sql, after)
    return "PG 日志观察: 验证窗口内新增了 %s 的记录（匹配记录数从 %s 变为 %s）。" % (sql, before, after)
