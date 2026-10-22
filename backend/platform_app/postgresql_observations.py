"""PostgreSQL 通用运行时与版本观测（``pg_stat_replication`` / ``SELECT version()``）。"""

import psycopg

from platform_app.observations import ParsedObservation, parse_tsv_rows
from platform_app.providers import Observation


def parse_replication(text: str) -> list[ParsedObservation]:
    result = []
    for row in parse_tsv_rows(text):
        identity = row.get("application_name") or row.get("client_addr")
        if not identity:
            continue
        state = (row.get("state") or "unknown").lower()
        result.append(
            ParsedObservation(
                "postgres.replication", state, {"entity_id": identity, **row}
            )
        )
    return result


def observe_pg_replication(environment: dict, parse_fn=parse_replication) -> list[ParsedObservation]:
    connection = psycopg.connect(
        host=environment["host"],
        port=environment["port"],
        dbname=environment.get("database_name") or "postgres",
        user=environment.get("database_user") or "postgres",
        connect_timeout=4,
        options="-c statement_timeout=4000",
    )
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT application_name, client_addr::text, state, sync_state "
                "FROM pg_stat_replication ORDER BY application_name"
            )
            rows = cursor.fetchall()
        text = "application_name\tclient_addr\tstate\tsync_state\n" + "\n".join(
            "\t".join("" if value is None else str(value) for value in row)
            for row in rows
        )
        return parse_fn(text)
    finally:
        connection.close()


def observe_pg_version(environment: dict) -> list[Observation]:
    connection = psycopg.connect(
        host=environment["host"],
        port=environment["port"],
        dbname=environment.get("database_name") or "postgres",
        user=environment.get("database_user") or "postgres",
        connect_timeout=4,
        options="-c statement_timeout=4000",
    )
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT version()")
            version = cursor.fetchone()[0]
        return [Observation("sql.version", "ready", {"version": version})]
    finally:
        connection.close()
