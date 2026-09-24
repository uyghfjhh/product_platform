"""Global-cache console snapshot model."""


GLOBAL_STATS_HEADERS = (
    "total_entries", "capacity", "referenced_count", "unreferenced_count",
    "parse_valid_count", "parse_failed_count", "forced_write_count",
    "bypass_count", "heartbeat_bypass_count", "guc_bypass_count",
    "hits", "misses", "evictions",
)


def parse_stats(rows):
    stats = {}
    data_rows = list(rows[1:]) if rows else []
    if not data_rows:
        return stats
    first = data_rows[0]
    for index, key in enumerate(GLOBAL_STATS_HEADERS):
        stats[key] = first[index] if index < len(first) else ""
    stats["referenced_entries"] = stats.get("referenced_count", "")
    stats["unreferenced_entries"] = stats.get("unreferenced_count", "")
    stats["bypass_entries"] = stats.get("bypass_count", "")
    return stats


def _normalize_global_rows(rows):
    if not rows:
        return []
    headers = [value.strip().lower() for value in rows[0]]
    positions = {name: index for index, name in enumerate(headers)}
    required = ("global_name", "description", "sql_class",
                "has_bypass_response", "ref_count")
    if not all(name in positions for name in required):
        return list(rows[1:])
    normalized = []
    for row in rows[1:]:
        values = [row[positions[name]].strip() if positions[name] < len(row) else ""
                  for name in required]
        if values[3].lower() in ("true", "yes"):
            values[3] = "1"
        elif values[3].lower() in ("false", "no"):
            values[3] = "0"
        normalized.append(values)
    return normalized


def _normalize_server_rows(rows):
    if not rows:
        return []
    headers = [value.strip().lower() for value in rows[0]]
    positions = {name: index for index, name in enumerate(headers)}
    required = ("type", "user", "database", "sid", "definition", "refcount")
    if not all(name in positions for name in required):
        return list(rows[1:])
    return [[row[positions[name]].strip() if positions[name] < len(row) else ""
             for name in required] for row in rows[1:]]


def capture_global_cache_state(query, prefix, include_server=True, record=None):
    def execute(sql, stem):
        if record is None:
            return query(sql, stem)
        return query(sql, stem, record=record)

    global_rows = execute(
        "SHOW GLOBAL_PREPARED_STATEMENTS;", "%s_global_prepared_statements" % prefix
    )
    stats_rows = execute(
        "SHOW GLOBAL_PREPARED_STATEMENTS_STATS;",
        "%s_global_prepared_statements_stats" % prefix,
    )
    server_rows = []
    if include_server:
        server_rows = execute("SHOW SERVER_PREP_STMTS;", "%s_server_prep_stmts" % prefix)
    return {
        "global": _normalize_global_rows(global_rows),
        "stats": parse_stats(stats_rows),
        "server": _normalize_server_rows(server_rows),
    }
