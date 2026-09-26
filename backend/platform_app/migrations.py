"""Transactional SQLite schema migrations for platform metadata."""

import sqlite3

SCHEMA_VERSION = 2

V1 = (
    """CREATE TABLE IF NOT EXISTS environments (
        id TEXT PRIMARY KEY,
        product_id TEXT NOT NULL,
        title TEXT NOT NULL,
        host TEXT NOT NULL,
        port INTEGER NOT NULL,
        database_name TEXT NOT NULL,
        database_user TEXT NOT NULL,
        deployment_config TEXT,
        deployment_target TEXT,
        created_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS tasks (
        id TEXT PRIMARY KEY,
        environment_id TEXT NOT NULL REFERENCES environments(id),
        action TEXT NOT NULL,
        target TEXT NOT NULL,
        status TEXT NOT NULL,
        parameters TEXT NOT NULL,
        reason TEXT,
        submission_key TEXT UNIQUE,
        created_at TEXT NOT NULL,
        started_at TEXT,
        finished_at TEXT,
        cancel_requested INTEGER NOT NULL DEFAULT 0,
        process_id INTEGER,
        process_started_at TEXT,
        last_sequence INTEGER NOT NULL DEFAULT 0
    )""",
    """CREATE TABLE IF NOT EXISTS events (
        task_id TEXT NOT NULL REFERENCES tasks(id),
        sequence INTEGER NOT NULL,
        recorded_at TEXT NOT NULL,
        event_type TEXT NOT NULL,
        payload TEXT NOT NULL,
        PRIMARY KEY (task_id, sequence)
    )""",
    """CREATE TABLE IF NOT EXISTS results (
        product_id TEXT NOT NULL,
        environment_id TEXT NOT NULL,
        target TEXT NOT NULL,
        profile TEXT NOT NULL,
        status TEXT NOT NULL,
        reason TEXT,
        artifact_dir TEXT,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (product_id, environment_id, target, profile)
    )""",
    """CREATE TABLE IF NOT EXISTS knowledge_sources (
        id TEXT PRIMARY KEY,
        product_id TEXT NOT NULL,
        title TEXT NOT NULL,
        path TEXT NOT NULL,
        created_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS diagnoses (
        product_id TEXT NOT NULL,
        environment_id TEXT NOT NULL,
        target TEXT NOT NULL,
        profile TEXT NOT NULL,
        result_updated_at TEXT NOT NULL,
        evidence_hash TEXT NOT NULL,
        model TEXT NOT NULL,
        content TEXT NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY (product_id, environment_id, target, profile)
    )""",
)

V2 = (
    "CREATE INDEX IF NOT EXISTS tasks_environment_status_idx ON tasks(environment_id, status)",
    "CREATE INDEX IF NOT EXISTS results_environment_idx ON results(environment_id)",
    "CREATE INDEX IF NOT EXISTS diagnoses_environment_idx ON diagnoses(environment_id)",
)

MIGRATIONS = {1: V1, 2: V2}


def migrate(connection: sqlite3.Connection) -> None:
    """Apply each version and its marker atomically under a write lock."""
    connection.execute("BEGIN IMMEDIATE")
    try:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        row = connection.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'"
        ).fetchone()
        current = int(row[0]) if row else 0
        if current > SCHEMA_VERSION:
            raise RuntimeError(f"数据库 schema 版本过新: {current} > {SCHEMA_VERSION}")
        for version in range(current + 1, SCHEMA_VERSION + 1):
            for statement in MIGRATIONS[version]:
                connection.execute(statement)
            connection.execute(
                "INSERT INTO schema_meta(key,value) VALUES ('schema_version',?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(version),),
            )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
