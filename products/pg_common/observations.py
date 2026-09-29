"""PostgreSQL 复制观测解析（``pg_stat_replication`` 风格 TSV）。"""

from platform_app.observations import ParsedObservation, parse_tsv_rows


def parse_replication(text: str) -> list[ParsedObservation]:
    result = []
    for row in parse_tsv_rows(text):
        identity = row.get("application_name") or row.get("client_addr")
        if not identity:
            continue
        state = (row.get("state") or "unknown").lower()
        result.append(ParsedObservation("postgres.replication", state, {"entity_id": identity, **row}))
    return result
