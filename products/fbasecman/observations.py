"""Parse fbasecman console and PostgreSQL replication observations.

These parsers accept psql's unaligned, tab-separated output so the platform
stores fields instead of scraping human-facing report text.
"""

from platform_app.observations import ParsedObservation, parse_tsv_rows
from platform_app.postgresql_observations import parse_replication as parse_replication


def parse_psql_rows(text: str, required: set[str]) -> list[dict[str, str]]:
    """Read the aligned psql table embedded in a recorded console step."""
    lines = text.splitlines()
    for index, line in enumerate(lines[:-1]):
        if "|" not in line:
            continue
        headers = [part.strip() for part in line.split("|")]
        if not required.issubset(headers):
            continue
        separator = lines[index + 1].strip()
        if not separator or set(separator) - {"-", "+"}:
            continue
        rows = []
        for value_line in lines[index + 2:]:
            if "|" not in value_line:
                break
            values = [part.strip() for part in value_line.split("|")]
            if len(values) == len(headers):
                rows.append(dict(zip(headers, values)))
        return rows
    return []


def _rows(text: str, required: set[str]) -> list[dict[str, str]]:
    return parse_psql_rows(text, required) or [
        row for row in parse_tsv_rows(text) if required.issubset(row)
    ]


def parse_expanded_records(text: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    current: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("-[ RECORD"):
            if current:
                rows.append(current)
            current = {}
            continue
        if " | " in line and current is not None:
            key, value = line.split(" | ", 1)
            key, value = key.strip(), value.strip()
            if key and key.replace("_", "").isalnum():
                current[key] = value
    if current:
        rows.append(current)
    return rows


def parse_node_status(text: str) -> list[ParsedObservation]:
    result = []
    for row in _rows(text, {"node_name", "state"}):
        name = row.get("node_name")
        if not name:
            continue
        raw = (row.get("status") or row.get("state") or "unknown").lower()
        state = "ready" if raw in {"active", "up", "ready", "running"} else raw
        group = row.get("group_name") or "default"
        result.append(ParsedObservation("fbasecman.node", state, {
            **row, "entity_id": f"cman:node:{group}:{name}", "label": name,
        }))
    return result

def parse_group_routing(text: str) -> list[ParsedObservation]:
    result = []
    for row in _rows(text, {"group_name", "candidate_node", "route_status"}):
        group = row.get("group_name")
        candidate = row.get("candidate_node")
        if not group or not candidate:
            continue
        state = (row.get("route_status") or "unknown").lower()
        result.append(ParsedObservation("fbasecman.route", state, {
            **row, "entity_id": f"cman:route:{group}:{candidate}",
            "label": candidate,
        }))
    return result


def parse_node_monitor(text: str) -> list[ParsedObservation]:
    result = []
    rows = _rows(text, {"node_name", "effective_status"})
    if not rows:
        rows = [row for row in parse_expanded_records(text)
                if {"node_name", "effective_status"}.issubset(row)]
    for row in rows:
        name = row.get("node_name")
        if not name:
            continue
        raw = (row.get("effective_status") or row.get("status") or "unknown").lower()
        state = "ready" if raw in {"active", "ready", "healthy", "up", "read_write", "read_only"} else raw
        group = row.get("group_name") or row.get("cluster_name") or "default"
        result.append(ParsedObservation("fbasecman.monitor", state, {
            **row, "entity_id": f"cman:monitor:{group}:{name}", "label": name,
        }))
    return result


def parse_monitor_config(text: str) -> list[ParsedObservation]:
    rows = _rows(text, {"monitor_enabled", "monitor_period", "monitor_max_retries"})
    return [ParsedObservation("fbasecman.monitor.config", "enabled" if row.get("monitor_enabled", "").lower() == "true" else "disabled", {
        **row, "entity_id": "cman:monitor:config", "label": "monitor config",
    }) for row in rows]


def parse_group_members(text: str) -> list[ParsedObservation]:
    rows = _rows(text, {"group_name", "node_name", "group_role", "state"})
    result = []
    for row in rows:
        node = row.get("node_name")
        if not node:
            continue
        raw = (row.get("state") or "unknown").lower()
        state = "ready" if raw in {"active", "ready", "up"} else raw
        group = row.get("group_name") or row.get("cluster_name") or "default"
        result.append(ParsedObservation("fbasecman.mmr.member", state, {
            **row, "entity_id": f"cman:node:{group}:{node}", "label": node,
        }))
    return result


def parse_nodes(text: str) -> list[ParsedObservation]:
    rows = _rows(text, {"node_name", "effective_role", "effective_status"})
    result = []
    for row in rows:
        node = row.get("node_name")
        if not node:
            continue
        raw = (row.get("effective_status") or "unknown").lower()
        state = "ready" if raw in {"write_only", "read_only", "read_write", "active"} else raw
        group = row.get("group_name") or row.get("storage_db") or "default"
        result.append(ParsedObservation("fbasecman.node.effective", state, {
            **row, "entity_id": f"cman:node:{group}:{node}", "label": node,
        }))
    return result


def parse_groups(text: str) -> list[ParsedObservation]:
    rows = _rows(text, {"group_name", "group_mode", "write_cluster", "promoted_cluster"})
    result = []
    for row in rows:
        group = row.get("group_name")
        if not group:
            continue
        mode = (row.get("group_mode") or "unknown").lower()
        result.append(ParsedObservation("fbasecman.mmr.group", "ready", {
            **row, "entity_id": f"cman:group:{group}", "label": group,
            "mode": mode,
        }))
    return result
