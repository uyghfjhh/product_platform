"""SQLite 元数据存储；短事务避免长任务占用写锁。"""

import json
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from .migrations import SCHEMA_VERSION as CURRENT_SCHEMA_VERSION
from .migrations import migrate


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


class ConflictError(RuntimeError):
    pass


class Store:
    SCHEMA_VERSION = CURRENT_SCHEMA_VERSION

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
            migrate(connection)

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

    def delete_environment(self, environment_id: str) -> bool:
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            active = connection.execute(
                "SELECT 1 FROM tasks WHERE environment_id=? AND status IN ('QUEUED','RUNNING','CANCELLING') LIMIT 1",
                (environment_id,),
            ).fetchone()
            if active:
                connection.rollback()
                raise ConflictError("环境仍有未结束的任务")
            connection.execute(
                "DELETE FROM events WHERE task_id IN (SELECT id FROM tasks WHERE environment_id=?)",
                (environment_id,),
            )
            connection.execute("DELETE FROM results WHERE environment_id=?", (environment_id,))
            connection.execute("DELETE FROM diagnoses WHERE environment_id=?", (environment_id,))
            connection.execute("DELETE FROM tasks WHERE environment_id=?", (environment_id,))
            changed = connection.execute("DELETE FROM environments WHERE id=?", (environment_id,)).rowcount
            connection.commit()
        return bool(changed)

    def create_task(
        self,
        environment_id: str,
        action: str,
        target: str,
        parameters: dict,
        submission_key: str | None,
    ) -> dict:
        task, _created = self.create_task_once(
            environment_id, action, target, parameters, submission_key,
        )
        return task

    def create_task_once(
        self,
        environment_id: str,
        action: str,
        target: str,
        parameters: dict,
        submission_key: str | None,
    ) -> tuple[dict, bool]:
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
                    return dict(found), False
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
        return self.get_task(task_id) or {}, True

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

    def finish_task(self, task_id: str, expected: tuple[str, ...], status: str,
                    reason: str | None) -> bool:
        """Publish terminal status and completion event in one transaction."""
        if status not in {"SUCCEEDED", "FAILED", "CANCELLED", "RECOVERY_REQUIRED"}:
            raise ValueError("任务终态无效")
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT status,last_sequence FROM tasks WHERE id=?", (task_id,),
            ).fetchone()
            if row is None or row["status"] not in expected:
                connection.rollback()
                return False
            at = now()
            sequence = row["last_sequence"] + 1
            connection.execute(
                "UPDATE tasks SET status=?,reason=?,finished_at=?,last_sequence=? WHERE id=?",
                (status, reason, at, sequence, task_id),
            )
            connection.execute(
                "INSERT INTO events(task_id,sequence,recorded_at,event_type,payload) VALUES (?,?,?,?,?)",
                (task_id, sequence, at, "operation.finished",
                 json.dumps({"status": status, "reason": reason}, ensure_ascii=False)),
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

    def get_result(self, product_id: str, environment_id: str, target: str,
                   profile: str = "default") -> dict | None:
        with self.connect() as connection:
            return self._dict(connection.execute(
                "SELECT * FROM results WHERE product_id=? AND environment_id=? AND target=? AND profile=?",
                (product_id, environment_id, target, profile),
            ).fetchone())

    def put_diagnosis(self, result: dict, evidence_hash: str, model: str,
                      content: dict) -> None:
        with self.connect() as connection:
            connection.execute("""
                INSERT INTO diagnoses(product_id,environment_id,target,profile,result_updated_at,
                                      evidence_hash,model,content,created_at)
                VALUES (?,?,?,?,?,?,?,?,?)
                ON CONFLICT(product_id,environment_id,target,profile) DO UPDATE SET
                    result_updated_at=excluded.result_updated_at,
                    evidence_hash=excluded.evidence_hash,model=excluded.model,
                    content=excluded.content,created_at=excluded.created_at
            """, (
                result["product_id"], result["environment_id"], result["target"],
                result["profile"], result["updated_at"], evidence_hash, model,
                json.dumps(content, ensure_ascii=False), now(),
            ))

    def get_diagnosis(self, product_id: str, environment_id: str, target: str,
                      profile: str = "default") -> dict | None:
        with self.connect() as connection:
            row = self._dict(connection.execute("""
                SELECT d.*, r.updated_at AS current_result_updated_at
                FROM diagnoses d LEFT JOIN results r ON
                    r.product_id=d.product_id AND r.environment_id=d.environment_id
                    AND r.target=d.target AND r.profile=d.profile
                WHERE d.product_id=? AND d.environment_id=? AND d.target=? AND d.profile=?
            """, (product_id, environment_id, target, profile)).fetchone())
        if row is None:
            return None
        row["content"] = json.loads(row["content"])
        row["stale"] = row["result_updated_at"] != row.pop("current_result_updated_at")
        return row
