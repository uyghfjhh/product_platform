"""Pure helper functions and parsers for handover suite operations and tests."""

import re
import time
from framework.clients.psql import parse_psql_table, build_psql_command
from framework.execution.shell import quote_arguments


def _pgbench_transactions(output):
    if "process group terminated" in output or "timeout after" in output:
        return 0
    match = re.search(r"number of transactions actually processed:\s*(\d+)", output)
    return int(match.group(1)) if match else 0


def _server_request_counts(output):
    read_total = 0
    write_total = 0
    for row in parse_psql_table(output):
        read_total += int(row.get("read_request_count", row.get("read_request", 0)) or 0)
        write_total += int(row.get("write_request_count", row.get("write_request", 0)) or 0)
    return read_total, write_total


def _pgbench_summary(output):
    lines = []
    for line in output.splitlines():
        if "client " in line and "sending " in line:
            continue
        if any(k in line for k in ("transactions actually processed", "number of failed", "tps =")):
            lines.append(line.strip())
    return "\n".join(lines)


def _marker_value(text, marker):
    match = re.search(r"%s=([^\r\n]+)" % re.escape(marker), text)
    return match.group(1).strip() if match else ""


def _server_connection_keys(output):
    keys = set()
    for row in parse_psql_table(output):
        # ignore changing statistics like read_request or create_time
        key = (row.get("node_name"), row.get("user"), row.get("database"), row.get("state"), row.get("ptr"))
        keys.add(key)
    return keys


def _global_cache_rows(output):
    rows = [line for line in output.splitlines() if "|" in line]
    if len(rows) < 2:
        return []
    headers = [field.strip().lower() for field in rows[0].split("|")]
    result = []
    for row in rows[1:]:
        if set(row.strip()) <= {"-", "+", " "}:
            continue
        values = [field.strip() for field in row.split("|")]
        item = dict(zip(headers, values))
        if "has_bypass_response" in item:
            item["has_bypass_response"] = {
                "true": "1", "yes": "1", "false": "0", "no": "0",
            }.get(item["has_bypass_response"].lower(), item["has_bypass_response"])
        result.append(item)
    return result


def _cache_row_matches(output, description, sql_class, bypass, ref_count=None):
    for row in _global_cache_rows(output):
        if row.get("description") == description:
            return (row.get("sql_class") == sql_class and row.get("has_bypass_response") == bypass and
                    (ref_count is None or row.get("ref_count") == ref_count))
    return False


def _psql_row_count(output):
    match = re.search(r"\((\d+)\s+rows?\)", output)
    if match:
        return int(match.group(1))
    return len(parse_psql_table(output))


def _jdbc_read_backend_pairs(output):
    pairs = {}
    for line in output.splitlines():
        m = re.search(r"(\w+)_(READ_BEFORE|READ_AFTER)\s+backend_port=(\d+)", line)
        if m:
            group, stage, port = m.group(1), m.group(2), m.group(3)
            pairs.setdefault(group, {})
            if stage == "READ_BEFORE":
                pairs[group]["before"] = port
            else:
                pairs[group]["after"] = port
    return pairs


def _psql_table_records(output):
    return parse_psql_table(output)


def _record_sum(records, key):
    return sum(int(r.get(key, 0) or 0) for r in records)


def _average_percent(records, key):
    vals = []
    for r in records:
        v = r.get(key, "0").rstrip("%")
        try:
            vals.append(float(v))
        except ValueError:
            pass
    return sum(vals) / len(vals) if vals else 0.0


def _thread_ratio_actual(records):
    return "\n".join(
        "thread_id=%s read_ratio=%s write_ratio=%s" % (
            row.get("thread_id", "<missing>"), row.get("read_ratio", "<missing>"),
            row.get("write_ratio", "<missing>"),
        )
        for row in records
    ) or "<无 worker 统计行>"


def _wait_for_thread_statistics_profile(rt, timeout_seconds):
    """Wait for all clients and the document's 3:2 workload ratio to settle."""
    command = build_psql_command(
        rt.env.config["local"]["postgres_dir"], "127.0.0.1", rt.listen_port,
        "admin", "console", "SHOW THREAD_STATUS;",
    )
    deadline = time.time() + timeout_seconds
    attempt = 0
    last_output = ""
    while time.time() < deadline:
        attempt += 1
        rc, output = rt.run_command(
            command, rt.logs_dir / ("thread_stats_warmup_%02d.log" % attempt),
            check=False, record=False,
        )
        last_output = output
        if rc == 0:
            rows = _psql_table_records(output)
            read_ratio = _average_percent(rows, "read_ratio")
            write_ratio = _average_percent(rows, "write_ratio")
            if (len(rows) == 10 and _record_sum(rows, "cl_connected") == 252 and
                    read_ratio is not None and write_ratio is not None and
                    abs(read_ratio - 60.0) <= 5.0 and
                    abs(write_ratio - 40.0) <= 5.0):
                return output
        time.sleep(0.5)
    return last_output


def _business_pool_records(output):
    return [r for r in parse_psql_table(output) if r.get("database") != "console"]


def _business_pools_are_stable(before, after):
    if len(before) != len(after):
        return False
    for b, a in zip(before, after):
        if b.get("total_requests") != a.get("total_requests"):
            return False
    return True


def _active_server_record(output, port):
    for r in parse_psql_table(output):
        if r.get("port") == str(port) and r.get("offline") in ("0", "false"):
            return r
    return {}


def _server_is_offline(output, ptr):
    for r in parse_psql_table(output):
        if r.get("ptr") == ptr:
            return r.get("offline") in ("1", "true")
    return False


def _parse_failure_log_evidence(proxy):
    """Keep the exact extended-protocol failure and cache-cleanup evidence."""
    markers = ("errorresponse", "deleted backend cache", "cleared p", "cleared b",
               "cleared d", "cleared e", "cleared s")
    lines = [line for line in proxy.splitlines() if any(marker in line.lower() for marker in markers)]
    return "\n".join(lines) if lines else "<未采集到 Parse 失败的代理日志>"


_GLOBAL_PS_ROWS = (
    ("DELETE FROM handover_global_ps WHERE id IN (1, 2, 3)", "NORMAL"),
    ("INSERT INTO handover_global_ps(id, note) VALUES ($1, $2)", "NORMAL"),
    ("SELECT note FROM handover_global_ps WHERE id = $1 /* handover_global_ps */", "NORMAL"),
    ("SELECT inet_server_port() AS backend_port, pg_is_in_recovery() AS in_recovery", "NORMAL"),
    ("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY", "RW_HINT_READ"),
    ("SET SESSION CHARACTERISTICS AS TRANSACTION READ WRITE", "RW_HINT_WRITE"),
    ("BEGIN READ ONLY", "BEGIN_READ_ONLY"),
)

_GLOBAL_PS_FINAL_REF_COUNTS = {
    "DELETE FROM handover_global_ps WHERE id IN (1, 2, 3)": "1",
    "INSERT INTO handover_global_ps(id, note) VALUES ($1, $2)": "1",
    "SELECT note FROM handover_global_ps WHERE id = $1 /* handover_global_ps */": "3",
    "SELECT inet_server_port() AS backend_port, pg_is_in_recovery() AS in_recovery": "3",
    "SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY": "0",
    "SET SESSION CHARACTERISTICS AS TRANSACTION READ WRITE": "0",
    "BEGIN READ ONLY": "1",
}

_GLOBAL_PS_PHASE_COUNTS = {
    "PREPARED_ROWS": 2,
    "DEFAULT": 4,
    "READ_ONLY_ONE": 5,
    "READ_WRITE_ONE": 6,
    "READ_ONLY_TWO": 6,
    "READ_WRITE_TWO": 6,
    "BEGIN_READ_ONLY": 7,
}


def _global_ps_contract_table(expected_count, include_ref_counts=False):
    lines = [
        "global_name | sql_class | has_bypass_response | ref_count | description",
        "------------+-----------+---------------------+-----------+------------",
    ]
    for index, (description, sql_class) in enumerate(_GLOBAL_PS_ROWS[:expected_count], 1):
        ref_count = _GLOBAL_PS_FINAL_REF_COUNTS[description] if include_ref_counts else "-"
        lines.append(
            "__fbasecman_%s | %s | 0 | %s | %s" %
            (index, sql_class, ref_count, description)
        )
    return "\n".join(lines)


def _validate_global_ps_rows(output, expected_count, check_final_ref_counts=False):
    """Validate chapter 11.5 cache rows without relying on output order."""
    rows = _global_cache_rows(output)
    expected_rows = _GLOBAL_PS_ROWS[:expected_count]
    expected_by_description = dict(expected_rows)
    descriptions = [row.get("description") for row in rows]
    names = [row.get("global_name", "") for row in rows]
    ids = []
    valid_names = True
    for name in names:
        match = re.match(r"^__fbasecman_([1-9][0-9]*)$", name)
        if not match:
            valid_names = False
        else:
            ids.append(int(match.group(1)))
    field_errors = []
    for description, sql_class in expected_rows:
        row = next((item for item in rows if item.get("description") == description), None)
        if row is None:
            field_errors.append("缺少 %s" % description)
        elif row.get("sql_class") != sql_class or row.get("has_bypass_response") != "0":
            field_errors.append(
                "%s: sql_class=%s, has_bypass_response=%s" % (
                    description, row.get("sql_class"), row.get("has_bypass_response"),
                )
            )
        elif check_final_ref_counts:
            actual_ref = row.get("ref_count", "")
            if description.startswith("SELECT"):
                # 文档 6410 行：ref_count 可能因连接断开或后端释放变化，仅供参考，不作为固定验收值
                if not actual_ref.isdigit() or int(actual_ref) < 0:
                    field_errors.append(
                        "%s: 无效 ref_count=%s" % (description, actual_ref)
                    )
            else:
                expected_ref = _GLOBAL_PS_FINAL_REF_COUNTS.get(description)
                if actual_ref != expected_ref:
                    field_errors.append(
                        "%s: ref_count=%s (expected %s)" % (description, actual_ref, expected_ref)
                    )
    passed = (
        len(rows) == expected_count and
        set(descriptions) == set(expected_by_description) and
        valid_names and len(set(names)) == expected_count and
        sorted(ids) == list(range(1, expected_count + 1)) and
        not field_errors
    )
    def row_sort_key(row):
        match = re.match(r"^__fbasecman_([1-9][0-9]*)$", row.get("global_name", ""))
        return int(match.group(1)) if match else 10 ** 9

    rendered_rows = [
        "%s | %s | %s | %s | %s" % (
            row.get("global_name", "<missing>"), row.get("sql_class", "<missing>"),
            row.get("has_bypass_response", "<missing>"), row.get("ref_count", "<missing>"),
            row.get("description", "<missing>"),
        )
        for row in sorted(rows, key=row_sort_key)
    ]
    actual = "rows=%s; cache_rows:\n%s\nfield_errors=%s" % (
        len(rows), "\n".join([
            "global_name | sql_class | has_bypass_response | ref_count | description",
            "------------+-----------+---------------------+-----------+------------",
        ] + (rendered_rows or ["<empty>"])),
        "; ".join(field_errors) or "<none>",
    )
    return passed, actual


def _global_ps_final_entries_expected():
    return "7 条全局缓存；预期缓存行:\n%s" % _global_ps_contract_table(7, True)


def _global_ps_phase_expected(expected_count):
    return (
        "%s 条缓存；预期缓存行:\n%s" %
        (expected_count, _global_ps_contract_table(expected_count))
    )


def _global_ps_jdbc_phase_result(phase, output, write_port):
    """Check the document's JDBC result and routing at each cache checkpoint."""
    if phase == "PREPARED_ROWS":
        passed = (
            "SQL=DELETE FROM handover_global_ps WHERE id IN (1, 2, 3)" in output and
            all("SQL=INSERT INTO handover_global_ps(id, note) VALUES (%s, 'name%s')" % (index, index)
                in output for index in (1, 2, 3))
        )
        return (
            "PreparedStatement DELETE 和 id=1..3 的 INSERT 均已执行",
            "DELETE/INSERT=%s" % ("完整" if passed else "缺失"),
            passed,
        )

    expected_ids = {
        "DEFAULT": 1,
        "READ_ONLY_ONE": 2,
        "READ_WRITE_ONE": 1,
        "READ_ONLY_TWO": 3,
        "READ_WRITE_TWO": 2,
        "BEGIN_READ_ONLY": 3,
    }
    expected_id = expected_ids.get(phase, 1)
    match = re.search(
        r"%s id=(\d+) note=([^\n]+)\s+%s backend_port=(\d+) in_recovery=(true|false)" %
        (re.escape(phase), re.escape(phase)), output,
    )
    if not match:
        return (
            "%s 返回 id=%s, note=name%s，并输出 backend_port/in_recovery" %
            (phase, expected_id, expected_id),
            "%s JDBC 输出中未找到该阶段的查询结果和后端信息" % phase,
            False,
        )
    actual_id, note, backend_port, in_recovery = match.groups()
    is_read = phase in ("READ_ONLY_ONE", "READ_ONLY_TWO", "BEGIN_READ_ONLY")
    route_passed = backend_port != str(write_port) if is_read else (
        backend_port == str(write_port) and in_recovery == "false"
    )
    passed = actual_id == str(expected_id) and note == "name%s" % expected_id and route_passed
    route_expected = ("非写主节点" if is_read else "写主节点 %s，in_recovery=false" % write_port)
    return (
        "%s 返回 id=%s, note=name%s；路由到%s" % (phase, expected_id, expected_id, route_expected),
        "%s id=%s, note=%s, backend_port=%s, in_recovery=%s" %
        (phase, actual_id, note, backend_port, in_recovery),
        passed,
    )


def _stats_values(output):
    rows = [line for line in output.splitlines() if "|" in line]
    if len(rows) < 2:
        return {}
    names = [part.strip().lower() for part in rows[0].split("|")]
    values = [part.strip() for part in rows[1].split("|")]
    result = {}
    for name, value in zip(names, values):
        if value.isdigit():
            result[name] = int(value)
    if "referenced_count" in result:
        result["referenced_entries"] = result["referenced_count"]
    if "unreferenced_count" in result:
        result["unreferenced_entries"] = result["unreferenced_count"]
    if "bypass_count" in result:
        result["bypass_entries"] = result["bypass_count"]
    return result


def _global_ps_phase_validator(first_run, write_port):
    """Build phase-local assertions from document 11.5's JDBC sequence."""
    def validate(phase, observed):
        expected_count = _GLOBAL_PS_PHASE_COUNTS.get(phase, 7) if first_run else 7
        results = observed.get("query_results", {})
        cache_output = results.get("SHOW GLOBAL_PREPARED_STATEMENTS;", "")
        stats_output = results.get("SHOW GLOBAL_PREPARED_STATEMENTS_STATS;", "")
        rows_passed, rows_actual = _validate_global_ps_rows(cache_output, expected_count)
        stats = _stats_values(stats_output)
        jdbc_expected, jdbc_actual, jdbc_passed = _global_ps_jdbc_phase_result(
            phase, observed.get("jdbc_output", ""), write_port,
        )
        if first_run:
            stats_passed = all(
                stats.get(name) == value
                for name, value in {
                    "total_entries": expected_count,
                    "bypass_entries": 0,
                    "misses": expected_count,
                    "evictions": 0,
                }.items()
            ) and stats.get("hits", -1) >= 0
            expected = (
                "%s；%s；统计 total_entries=%s, bypass_entries=0, hits>=0, "
                "misses=%s, evictions=0（hits 为阶段累计值，首轮结束时统一对账）" % (
                    _global_ps_phase_expected(expected_count), jdbc_expected,
                    expected_count, expected_count,
                )
            )
        else:
            stats_passed = (
                stats.get("total_entries") == 7 and
                stats.get("bypass_entries") == 0 and
                stats.get("evictions") == 0
            )
            expected = (
                "%s；%s；第二次执行不新增条目，统计 total_entries=7, "
                "bypass_entries=0, evictions=0" % (
                    _global_ps_phase_expected(expected_count), jdbc_expected,
                )
            )
        return {
            "expected": expected,
            "actual": "%s; %s; stats=%s" % (
                jdbc_actual, rows_actual, stats or "<无法解析>",
            ),
            "passed": observed.get("passed", True) and jdbc_passed and rows_passed and stats_passed,
        }
    return validate


def _phase_console_observation(rt, phase, queries, log_prefix):
    """Run console queries while a JDBC driver is paused at a phase marker."""
    observations = []
    query_results = {}
    passed = True
    for index, sql in enumerate(queries, 1):
        command = build_psql_command(
            rt.env.config["local"]["postgres_dir"], "127.0.0.1", rt.listen_port,
            "admin", "console", sql,
        )
        logfile = rt.logs_dir / ("%s_%s_%02d.log" % (log_prefix, phase.lower(), index))
        rc, output = rt.run_command(command, logfile, check=False, record=False)
        query_results[sql] = output
        observations.append(
            "$ %s\n%s" % (quote_arguments(command), output.rstrip() or "<empty>")
        )
        passed = passed and rc == 0
    return {
        "command": "阶段 %s: 依次执行以下 console 查询" % phase,
        "output": "\n\n".join(observations),
        "passed": passed,
        "actual": "%s 阶段 console 查询%s" % (phase, "成功" if passed else "失败"),
        "query_results": query_results,
    }


__all__ = [
    "_pgbench_transactions",
    "_server_request_counts",
    "_pgbench_summary",
    "_marker_value",
    "_server_connection_keys",
    "_global_cache_rows",
    "_cache_row_matches",
    "_psql_row_count",
    "_jdbc_read_backend_pairs",
    "_psql_table_records",
    "_record_sum",
    "_average_percent",
    "_thread_ratio_actual",
    "_wait_for_thread_statistics_profile",
    "_business_pool_records",
    "_business_pools_are_stable",
    "_active_server_record",
    "_server_is_offline",
    "_parse_failure_log_evidence",
    "_GLOBAL_PS_ROWS",
    "_GLOBAL_PS_FINAL_REF_COUNTS",
    "_GLOBAL_PS_PHASE_COUNTS",
    "_global_ps_contract_table",
    "_validate_global_ps_rows",
    "_global_ps_phase_expected",
    "_global_ps_jdbc_phase_result",
    "_stats_values",
    "_global_ps_phase_validator",
    "_phase_console_observation",
]
