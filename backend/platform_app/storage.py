"""SQLite 元数据存储；短事务避免长任务占用写锁。"""

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class ConflictError(RuntimeError):
    pass


class Store:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=10000")
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            yield connection
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS environments (
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
                );
                CREATE TABLE IF NOT EXISTS tasks (
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
                );
                CREATE TABLE IF NOT EXISTS events (
                    task_id TEXT NOT NULL REFERENCES tasks(id),
                    sequence INTEGER NOT NULL,
                    recorded_at TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    PRIMARY KEY (task_id, sequence)
                );
                CREATE TABLE IF NOT EXISTS results (
                    product_id TEXT NOT NULL,
                    environment_id TEXT NOT NULL,
                    target TEXT NOT NULL,
                    profile TEXT NOT NULL,
                    status TEXT NOT NULL,
                    reason TEXT,
                    artifact_dir TEXT,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (product_id, environment_id, target, profile)
                );
                CREATE TABLE IF NOT EXISTS knowledge_sources (
                    id TEXT PRIMARY KEY,
                    product_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    path TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
            """)

    @staticmethod
    def _dict(row: sqlite3.Row | None) -> dict | None:
        return dict(row) if row is not None else None

    def list_environments(self) -> list[dict]:
        with self.connect() as connection:
            return [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM environments ORDER BY title"
                )
            ]

    def get_environment(self, environment_id: str) -> dict | None:
        with self.connect() as connection:
            return self._dict(
                connection.execute(
                    "SELECT * FROM environments WHERE id=?", (environment_id,)
                ).fetchone()
            )

    def put_environment(self, payload: dict) -> dict:
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT id FROM environments WHERE id=?", (payload["id"],)
            ).fetchone()
            if existing:
                connection.rollback()
                raise ConflictError("环境 ID 已存在")
            connection.execute(
                """
                INSERT INTO environments
                (id,product_id,title,host,port,database_name,database_user,deployment_config,deployment_target,created_at)
                VALUES (:id,:product_id,:title,:host,:port,:database_name,:database_user,:deployment_config,:deployment_target,:created_at)
            """,
                {**payload, "created_at": now()},
            )
            connection.commit()
        return self.get_environment(payload["id"]) or {}

    def update_environment(self, environment_id: str, payload: dict) -> dict | None:
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            changed = connection.execute(
                """
                UPDATE environments SET product_id=:product_id,title=:title,host=:host,port=:port,
                    database_name=:database_name,database_user=:database_user,
                    deployment_config=:deployment_config,deployment_target=:deployment_target
                WHERE id=:id
            """,
                {**payload, "id": environment_id},
            ).rowcount
            connection.commit()
        return self.get_environment(environment_id) if changed else None

    def create_task(
        self,
        environment_id: str,
        action: str,
        target: str,
        parameters: dict,
        submission_key: str | None,
    ) -> dict:
        payload = json.dumps(parameters, ensure_ascii=False, sort_keys=True)
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if submission_key:
                found = connection.execute(
                    "SELECT * FROM tasks WHERE submission_key=?", (submission_key,)
                ).fetchone()
                if found:
                    if (
                        found["environment_id"] != environment_id
                        or found["action"] != action
                        or found["target"] != target
                        or found["parameters"] != payload
                    ):
                        connection.rollback()
                        raise ConflictError("提交标识已用于其他操作")
                    connection.commit()
                    return dict(found)
            task_id = str(uuid.uuid4())
            connection.execute(
                """
                INSERT INTO tasks(id,environment_id,action,target,status,parameters,submission_key,created_at)
                VALUES (?,?,?,?,?,?,?,?)
            """,
                (
                    task_id,
                    environment_id,
                    action,
                    target,
                    "QUEUED",
                    payload,
                    submission_key,
                    now(),
                ),
            )
            connection.commit()
        return self.get_task(task_id) or {}

    def get_task(self, task_id: str) -> dict | None:
        with self.connect() as connection:
            return self._dict(
                connection.execute(
                    "SELECT * FROM tasks WHERE id=?", (task_id,)
                ).fetchone()
            )

    def list_tasks(self, limit: int = 40) -> list[dict]:
        with self.connect() as connection:
            return [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM tasks ORDER BY created_at DESC LIMIT ?", (limit,)
                )
            ]

    def unfinished_tasks(self) -> list[dict]:
        with self.connect() as connection:
            return [dict(row) for row in connection.execute(
                "SELECT * FROM tasks WHERE status IN ('QUEUED','RUNNING','CANCELLING') ORDER BY created_at"
            )]

    def transition_task(
        self,
        task_id: str,
        expected: tuple[str, ...],
        status: str,
        *,
        reason: str | None = None,
        process_id: int | None = None,
    ) -> bool:
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT status FROM tasks WHERE id=?", (task_id,)
            ).fetchone()
            if not row or row["status"] not in expected:
                connection.rollback()
                return False
            started = now() if status == "RUNNING" else None
            finished = (
                now()
                if status in {"SUCCEEDED", "FAILED", "CANCELLED", "RECOVERY_REQUIRED"}
                else None
            )
            connection.execute(
                """
                UPDATE tasks SET status=?, reason=COALESCE(?,reason), process_id=COALESCE(?,process_id),
                    started_at=COALESCE(?,started_at), finished_at=COALESCE(?,finished_at)
                WHERE id=?
            """,
                (status, reason, process_id, started, finished, task_id),
            )
            connection.commit()
            return True

    def request_cancel(self, task_id: str) -> bool:
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT status FROM tasks WHERE id=?", (task_id,)
            ).fetchone()
            if not row or row["status"] not in {"QUEUED", "RUNNING"}:
                connection.rollback()
                return False
            status = "CANCELLED" if row["status"] == "QUEUED" else "CANCELLING"
            connection.execute(
                "UPDATE tasks SET status=?,cancel_requested=1,finished_at=? WHERE id=?",
                (status, now() if status == "CANCELLED" else None, task_id),
            )
            connection.commit()
            return True

    def add_event(self, task_id: str, event_type: str, payload: dict) -> dict:
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT last_sequence FROM tasks WHERE id=?", (task_id,)
            ).fetchone()
            if row is None:
                connection.rollback()
                raise KeyError(task_id)
            sequence = row["last_sequence"] + 1
            at = now()
            connection.execute(
                "INSERT INTO events(task_id,sequence,recorded_at,event_type,payload) VALUES (?,?,?,?,?)",
                (
                    task_id,
                    sequence,
                    at,
                    event_type,
                    json.dumps(payload, ensure_ascii=False),
                ),
            )
            connection.execute(
                "UPDATE tasks SET last_sequence=? WHERE id=?", (sequence, task_id)
            )
            connection.commit()
        return {
            "task_id": task_id,
            "sequence": sequence,
            "recorded_at": at,
            "event_type": event_type,
            "payload": payload,
        }

    def list_events(self, task_id: str, after: int = 0) -> list[dict]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM events WHERE task_id=? AND sequence>? ORDER BY sequence",
                (task_id, after),
            ).fetchall()
        return [{**dict(row), "payload": json.loads(row["payload"])} for row in rows]

    def put_result(
        self,
        product_id: str,
        environment_id: str,
        target: str,
        profile: str,
        status: str,
        reason: str | None,
        artifact_dir: str | None,
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO results(product_id,environment_id,target,profile,status,reason,artifact_dir,updated_at)
                VALUES (?,?,?,?,?,?,?,?)
                ON CONFLICT(product_id,environment_id,target,profile) DO UPDATE SET
                    status=excluded.status,reason=excluded.reason,artifact_dir=excluded.artifact_dir,updated_at=excluded.updated_at
            """,
                (
                    product_id,
                    environment_id,
                    target,
                    profile,
                    status,
                    reason,
                    artifact_dir,
                    now(),
                ),
            )

    def list_results(self, environment_id: str) -> list[dict]:
        with self.connect() as connection:
            return [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM results WHERE environment_id=? ORDER BY updated_at DESC",
                    (environment_id,),
                )
            ]
