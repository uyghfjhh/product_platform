"""Shared PostgreSQL replication observations used by database products."""

from dataclasses import dataclass


@dataclass(frozen=True)
class ParsedObservation:
    kind: str
    state: str
    details: dict[str, str]


def parse_tsv_rows(text: str) -> list[dict[str, str]]:
    """Parse unaligned psql rows while ignoring incomplete lines."""
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        return []
    headers = [item.strip() for item in lines[0].split("\t")]
    rows = []
    for line in lines[1:]:
        values = line.split("\t")
        if len(values) == len(headers):
            rows.append(dict(zip(headers, (value.strip() for value in values))))
    return rows


def parse_replication(text: str) -> list[ParsedObservation]:
    result = []
    for row in parse_tsv_rows(text):
        identity = row.get("application_name") or row.get("client_addr")
        if not identity:
            continue
        state = (row.get("state") or "unknown").lower()
        result.append(ParsedObservation("postgres.replication", state, {"entity_id": identity, **row}))
    return result
