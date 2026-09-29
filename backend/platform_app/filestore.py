"""文件元数据存储：与旧 SQLite Store 完全同构，数据落盘为 YAML/JSON/JSONL。

目录布局（root 为 settings.data_dir）::

    environments/<env_id>.yaml      环境记录
    bindings.yaml                   {product_id: {profile_id: record}}
    tasks/<task_id>/meta.json       任务元数据（含 last_sequence）
    tasks/<task_id>/events.jsonl    追加式事件日志
    results/<env_id>/<sha>.json     结果（product+target+profile 键哈希命名）
    diagnoses/<env_id>/<sha>.json   诊断（随 result 快照判定 stale）

写路径：临时文件 + os.replace 原子替换；多步写持 locks/store.lock 的
flock 独占锁，保证并发安全。
"""

import fcntl
import hashlib
import json
import logging
import os
import sqlite3
import tempfile
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import yaml


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


class ConflictError(RuntimeError):
    pass


logger = logging.getLogger(__name__)

ACTIVE_STATUSES = ("QUEUED", "RUNNING", "CANCELLING")
TERMINAL_STATUSES = {"SUCCEEDED", "FAILED", "CANCELLED", "RECOVERY_REQUIRED"}

_TASK_FIELDS = (
    "id", "environment_id", "action", "target", "status", "parameters",
    "submission_key", "created_at", "started_at", "finished_at",
    "reason", "process_id", "cancel_requested", "last_sequence",
)


class FileStore:
    """与旧 ``Store`` 相同的公共 API，底层为纯文件存储。"""

    def __init__(self, data_dir: Path):
        self.root = Path(data_dir)
        self.platform_dir = self.root / "platform"
        self.platform_dir.mkdir(parents=True, exist_ok=True)
        for sub in ("environments", "tasks", "results", "diagnoses", "locks"):
            (self.root / sub).mkdir(parents=True, exist_ok=True)
        self._import_legacy_sqlite()

    # ---- 底层原语 ---------------------------------------------------------

    @contextmanager
    def _locked(self) -> Iterator[None]:
        """跨写操作的进程内+进程间互斥（等价于旧 BEGIN IMMEDIATE）。"""
        lock_path = self.root / "locks" / "store.lock"
        with open(lock_path, "a", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    @staticmethod
    def _atomic_write(path: Path, content: str) -> None:
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=".part")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    @staticmethod
    def _read_yaml(path: Path, default=None):
        if not path.is_file():
            return default
        with open(path, encoding="utf-8") as handle:
            return yaml.safe_load(handle) or default

    @staticmethod
    def _read_json(path: Path, default=None):
        if not path.is_file():
            return default
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return default

    @staticmethod
    def _key(*parts: str) -> str:
        return hashlib.sha256("\x00".join(parts).encode("utf-8")).hexdigest()[:24]

    def _env_path(self, environment_id: str) -> Path:
        return self.root / "environments" / f"{environment_id}.yaml"

    def _task_dir(self, task_id: str) -> Path:
        return self.root / "tasks" / task_id

    def _task_meta(self, task_id: str) -> Path:
        return self._task_dir(task_id) / "meta.json"

    def _task_events(self, task_id: str) -> Path:
        return self._task_dir(task_id) / "events.jsonl"

    def _write_task(self, task: dict) -> None:
        path = self._task_meta(task["id"])
        path.parent.mkdir(parents=True, exist_ok=True)
        self._atomic_write(
            path, json.dumps(task, ensure_ascii=False, indent=2, sort_keys=True)
        )

    def _result_path(self, environment_id: str, product_id: str,
                     target: str, profile: str) -> Path:
        name = self._key(product_id, target, profile) + ".json"
        return self.root / "results" / environment_id / name

    def _diagnosis_path(self, environment_id: str, product_id: str,
                        target: str, profile: str) -> Path:
        name = self._key(product_id, target, profile) + ".json"
        return self.root / "diagnoses" / environment_id / name

    def _read_bindings(self) -> dict:
        return self._read_yaml(self.root / "bindings.yaml", {})

    def _write_bindings(self, data: dict) -> None:
        self._atomic_write(
            self.root / "bindings.yaml",
            yaml.safe_dump(data, allow_unicode=True, sort_keys=True),
        )

    def _task_rows(self) -> list[dict]:
        rows = []
        tasks_dir = self.root / "tasks"
        for meta in tasks_dir.glob("*/meta.json"):
            row = self._read_json(meta)
            if row:
                rows.append(row)
        return rows

    def _has_active_task(self, environment_id: str) -> bool:
        return any(
            row.get("environment_id") == environment_id
            and row.get("status") in ACTIVE_STATUSES
            for row in self._task_rows()
        )

    # ---- 旧 SQLite 数据一次性导入 ------------------------------------------

    def _import_legacy_sqlite(self) -> None:
        marker = self.root / ".sqlite_imported"
        legacy = self.platform_dir / "platform.sqlite3"
        if marker.exists() or not legacy.is_file():
            return
        try:
            with self._locked():
                if marker.exists():
                    return
                skipped = self._import_sqlite_rows(legacy)
                marker.write_text(now(), encoding="utf-8")
                if skipped:
                    logger.warning(
                        "旧 SQLite 导入跳过了 %d 条已存在的记录——"
                        "若这不是首次导入，请检查 .sqlite_imported 标记是否丢失",
                        skipped,
                    )
        except (sqlite3.Error, OSError, KeyError, ValueError) as exc:
            # 旧库损坏不应阻塞文件存储启动；skip-existing 使重试幂等
            logger.warning("旧 SQLite 数据导入失败并跳过: %s", exc)

    def _import_sqlite_rows(self, db_path: Path) -> int:
        """逐条导入旧库行；已存在的目标文件一律跳过，返回跳过计数。"""
        skipped = 0
        connection = sqlite3.connect(db_path, timeout=10)
        connection.row_factory = sqlite3.Row
        try:
            tables = {
                row[0] for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            if "environments" in tables:
                for row in connection.execute("SELECT * FROM environments"):
                    env = dict(row)
                    env.setdefault("created_at", now())
                    path = self._env_path(env["id"])
                    if path.exists():
                        skipped += 1
                        continue
                    self._atomic_write(
                        path,
                        yaml.safe_dump(env, allow_unicode=True, sort_keys=True),
                    )
            if "regression_bindings" in tables:
                bindings = self._read_bindings()
                for row in connection.execute("SELECT * FROM regression_bindings"):
                    record = dict(row)
                    profiles = bindings.setdefault(record["product_id"], {})
                    profile_id = record.get("profile_id", "default")
                    if profile_id in profiles:
                        skipped += 1
                        continue
                    profiles[profile_id] = record
                self._write_bindings(bindings)
            if "tasks" in tables:
                for row in connection.execute("SELECT * FROM tasks ORDER BY created_at"):
                    task = dict(row)
                    task.setdefault("last_sequence", 0)
                    task.setdefault("cancel_requested", 0)
                    if self._task_meta(task["id"]).exists():
                        skipped += 1
                        continue
                    self._write_task(task)
            if "events" in tables:
                grouped: dict[str, list[dict]] = {}
                for row in connection.execute(
                    "SELECT * FROM events ORDER BY task_id, sequence"
                ):
                    event = dict(row)
                    event["payload"] = json.loads(event["payload"])
                    grouped.setdefault(event["task_id"], []).append(event)
                for task_id, events in grouped.items():
                    events_file = self._task_events(task_id)
                    if events_file.exists():
                        skipped += 1
                        continue
                    events_file.parent.mkdir(parents=True, exist_ok=True)
                    self._atomic_write(
                        events_file,
                        "".join(
                            json.dumps(event, ensure_ascii=False) + "\n"
                            for event in events
                        ),
                    )
            if "results" in tables:
                for row in connection.execute("SELECT * FROM results"):
                    record = dict(row)
                    path = self._result_path(
                        record["environment_id"], record["product_id"],
                        record["target"], record["profile"],
                    )
                    if path.exists():
                        skipped += 1
                        continue
                    path.parent.mkdir(parents=True, exist_ok=True)
                    self._atomic_write(
                        path, json.dumps(record, ensure_ascii=False, indent=2)
                    )
            if "diagnoses" in tables:
                for row in connection.execute("SELECT * FROM diagnoses"):
                    record = dict(row)
                    record["content"] = json.loads(record["content"])
                    path = self._diagnosis_path(
                        record["environment_id"], record["product_id"],
                        record["target"], record["profile"],
                    )
                    if path.exists():
                        skipped += 1
                        continue
                    path.parent.mkdir(parents=True, exist_ok=True)
                    self._atomic_write(
                        path, json.dumps(record, ensure_ascii=False, indent=2)
                    )
        finally:
            connection.close()
        return skipped

    # ---- 环境 -------------------------------------------------------------

    def list_environments(self) -> list[dict]:
        envs = []
        for path in sorted(self._env_path("*").parent.glob("*.yaml")):
            env = self._read_yaml(path)
            if env:
                envs.append(env)
        envs.sort(key=lambda item: item.get("title") or "")
        return envs

    def get_environment(self, environment_id: str) -> dict | None:
        return self._read_yaml(self._env_path(environment_id))

    def put_environment(self, payload: dict) -> dict:
        with self._locked():
            if self._env_path(payload["id"]).exists():
                raise ConflictError("环境 ID 已存在")
            record = {**payload, "created_at": now()}
            self._atomic_write(
                self._env_path(payload["id"]),
                yaml.safe_dump(record, allow_unicode=True, sort_keys=True),
            )
        return self.get_environment(payload["id"]) or {}

    def update_environment(self, environment_id: str, payload: dict) -> dict | None:
        columns = (
            "product_id", "title", "host", "port", "database_name",
            "database_user", "deployment_config", "deployment_target",
        )
        with self._locked():
            env = self.get_environment(environment_id)
            if env is None:
                return None
            env.update({key: payload.get(key) for key in columns})
            self._atomic_write(
                self._env_path(environment_id),
                yaml.safe_dump(env, allow_unicode=True, sort_keys=True),
            )
        return self.get_environment(environment_id)

    def delete_environment(self, environment_id: str) -> bool:
        with self._locked():
            if not self._env_path(environment_id).exists():
                return False
            if self._has_active_task(environment_id):
                raise ConflictError("环境仍有未结束的任务")
            # 级联：任务目录（含事件）、结果、诊断、绑定引用
            for meta in (self.root / "tasks").glob("*/meta.json"):
                row = self._read_json(meta)
                if row and row.get("environment_id") == environment_id:
                    for child in meta.parent.iterdir():
                        child.unlink()
                    meta.parent.rmdir()
            for sub in ("results", "diagnoses"):
                env_dir = self.root / sub / environment_id
                if env_dir.is_dir():
                    for child in env_dir.iterdir():
                        child.unlink()
                    env_dir.rmdir()
            bindings = self._read_bindings()
            changed = False
            for product_id, profiles in list(bindings.items()):
                for profile_id, record in list(profiles.items()):
                    if record.get("environment_id") == environment_id:
                        del profiles[profile_id]
                        changed = True
                if not profiles:
                    del bindings[product_id]
            if changed:
                self._write_bindings(bindings)
            self._env_path(environment_id).unlink()
        return True

    # ---- 回归绑定 ----------------------------------------------------------

    def list_regression_bindings(self) -> list[dict]:
        rows = []
        for profiles in self._read_bindings().values():
            rows.extend(profiles.values())
        rows.sort(key=lambda item: (item.get("product_id") or "",
                                    item.get("profile_id") or ""))
        return [
            {"product_id": row["product_id"], "profile_id": row["profile_id"],
             "environment_id": row["environment_id"],
             "updated_at": row["updated_at"]}
            for row in rows
        ]

    def get_regression_binding(self, product_id: str,
                               profile_id: str) -> dict | None:
        record = self._read_bindings().get(product_id, {}).get(profile_id)
        if record is None:
            return None
        return {"product_id": record["product_id"],
                "profile_id": record["profile_id"],
                "environment_id": record["environment_id"],
                "updated_at": record["updated_at"]}

    def put_regression_binding(self, product_id: str, profile_id: str,
                               environment_id: str) -> dict:
        with self._locked():
            environment = self.get_environment(environment_id)
            if environment is None or environment.get("product_id") != product_id:
                raise ConflictError("回归测试只能绑定所属产品的环境")
            bindings = self._read_bindings()
            previous = bindings.get(product_id, {}).get(profile_id)
            affected = {environment_id}
            if previous:
                affected.add(previous["environment_id"])
            for affected_id in affected:
                if self._has_active_task(affected_id):
                    raise ConflictError("环境仍有未结束任务，暂不能变更回归绑定")
            record = {"product_id": product_id, "profile_id": profile_id,
                      "environment_id": environment_id, "updated_at": now()}
            bindings.setdefault(product_id, {})[profile_id] = record
            self._write_bindings(bindings)
        return self.get_regression_binding(product_id, profile_id) or {}

    def delete_regression_binding(self, product_id: str,
                                  profile_id: str) -> bool:
        with self._locked():
            bindings = self._read_bindings()
            existing = bindings.get(product_id, {}).get(profile_id)
            if existing is None:
                return False
            if self._has_active_task(existing["environment_id"]):
                raise ConflictError("环境仍有未结束任务，暂不能解除回归绑定")
            del bindings[product_id][profile_id]
            if not bindings[product_id]:
                del bindings[product_id]
            self._write_bindings(bindings)
        return True

    # ---- 任务 --------------------------------------------------------------

    def create_task(self, environment_id: str, action: str, target: str,
                    parameters: dict, submission_key: str | None) -> dict:
        task, _created = self.create_task_once(
            environment_id, action, target, parameters, submission_key)
        return task

    def create_task_once(self, environment_id: str, action: str, target: str,
                         parameters: dict,
                         submission_key: str | None) -> tuple[dict, bool]:
        payload = json.dumps(parameters, ensure_ascii=False, sort_keys=True)
        with self._locked():
            if submission_key:
                for row in self._task_rows():
                    if row.get("submission_key") != submission_key:
                        continue
                    if (row["environment_id"] != environment_id
                            or row["action"] != action
                            or row["target"] != target
                            or row["parameters"] != payload):
                        raise ConflictError("提交标识已用于其他操作")
                    return dict(row), False
            task = {
                "id": str(uuid.uuid4()),
                "environment_id": environment_id,
                "action": action,
                "target": target,
                "status": "QUEUED",
                "parameters": payload,
                "submission_key": submission_key,
                "created_at": now(),
                "started_at": None,
                "finished_at": None,
                "reason": None,
                "process_id": None,
                "cancel_requested": 0,
                "last_sequence": 0,
            }
            self._write_task(task)
        return task, True

    def get_task(self, task_id: str) -> dict | None:
        return self._read_json(self._task_meta(task_id))

    def list_tasks(self, limit: int = 40) -> list[dict]:
        rows = self._task_rows()
        rows.sort(key=lambda item: item.get("created_at") or "", reverse=True)
        return rows[:limit]

    def unfinished_tasks(self) -> list[dict]:
        rows = [row for row in self._task_rows()
                if row.get("status") in ACTIVE_STATUSES]
        rows.sort(key=lambda item: item.get("created_at") or "")
        return rows

    def transition_task(self, task_id: str, expected: tuple[str, ...],
                        status: str, *, reason: str | None = None,
                        process_id: int | None = None) -> bool:
        with self._locked():
            task = self.get_task(task_id)
            if not task or task["status"] not in expected:
                return False
            task["status"] = status
            if reason is not None:
                task["reason"] = reason
            if process_id is not None:
                task["process_id"] = process_id
            if status == "RUNNING" and task.get("started_at") is None:
                task["started_at"] = now()
            if status in TERMINAL_STATUSES and task.get("finished_at") is None:
                task["finished_at"] = now()
            self._write_task(task)
        return True

    def request_cancel(self, task_id: str) -> bool:
        with self._locked():
            task = self.get_task(task_id)
            if not task or task["status"] not in {"QUEUED", "RUNNING"}:
                return False
            if task["status"] == "QUEUED":
                task["status"] = "CANCELLED"
                task["finished_at"] = now()
            else:
                task["status"] = "CANCELLING"
            task["cancel_requested"] = 1
            self._write_task(task)
        return True

    def finish_task(self, task_id: str, expected: tuple[str, ...], status: str,
                    reason: str | None) -> bool:
        """终态 + operation.finished 事件原子提交。"""
        if status not in TERMINAL_STATUSES:
            raise ValueError("任务终态无效")
        with self._locked():
            task = self.get_task(task_id)
            if task is None or task["status"] not in expected:
                return False
            at = now()
            task["status"] = status
            task["reason"] = reason
            task["finished_at"] = at
            task["last_sequence"] = task.get("last_sequence", 0) + 1
            self._write_task(task)
            self._append_event(task, "operation.finished",
                               {"status": status, "reason": reason}, at)
        return True

    # ---- 事件 --------------------------------------------------------------

    def _append_event(self, task: dict, event_type: str, payload: dict,
                      at: str | None = None) -> dict:
        event = {
            "task_id": task["id"],
            "sequence": task["last_sequence"],
            "recorded_at": at or now(),
            "event_type": event_type,
            "payload": payload,
        }
        events_file = self._task_events(task["id"])
        events_file.parent.mkdir(parents=True, exist_ok=True)
        with open(events_file, "a", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                handle.write(json.dumps(event, ensure_ascii=False) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        return event

    def add_event(self, task_id: str, event_type: str, payload: dict) -> dict:
        with self._locked():
            task = self.get_task(task_id)
            if task is None:
                raise KeyError(task_id)
            task["last_sequence"] = task.get("last_sequence", 0) + 1
            self._write_task(task)
            event = self._append_event(task, event_type, payload)
        return {
            "task_id": task_id,
            "sequence": event["sequence"],
            "recorded_at": event["recorded_at"],
            "event_type": event_type,
            "payload": payload,
        }

    def list_events(self, task_id: str, after: int = 0) -> list[dict]:
        events_file = self._task_events(task_id)
        if not events_file.is_file():
            return []
        events = []
        for line in events_file.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("sequence", 0) > after:
                events.append(event)
        events.sort(key=lambda item: item.get("sequence", 0))
        return events

    # ---- 结果与诊断 ---------------------------------------------------------

    def put_result(self, product_id: str, environment_id: str, target: str,
                   profile: str, status: str, reason: str | None,
                   artifact_dir: str | None) -> None:
        record = {
            "product_id": product_id,
            "environment_id": environment_id,
            "target": target,
            "profile": profile,
            "status": status,
            "reason": reason,
            "artifact_dir": artifact_dir,
            "updated_at": now(),
        }
        path = self._result_path(environment_id, product_id, target, profile)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._atomic_write(path, json.dumps(record, ensure_ascii=False, indent=2))

    def list_results(self, environment_id: str) -> list[dict]:
        env_dir = self.root / "results" / environment_id
        rows = []
        if env_dir.is_dir():
            for path in env_dir.glob("*.json"):
                record = self._read_json(path)
                if record:
                    rows.append(record)
        rows.sort(key=lambda item: item.get("updated_at") or "", reverse=True)
        return rows

    def get_result(self, product_id: str, environment_id: str, target: str,
                   profile: str = "default") -> dict | None:
        return self._read_json(
            self._result_path(environment_id, product_id, target, profile))

    def put_diagnosis(self, result: dict, evidence_hash: str, model: str,
                      content: dict) -> None:
        record = {
            "product_id": result["product_id"],
            "environment_id": result["environment_id"],
            "target": result["target"],
            "profile": result["profile"],
            "result_updated_at": result["updated_at"],
            "evidence_hash": evidence_hash,
            "model": model,
            "content": content,
            "created_at": now(),
        }
        path = self._diagnosis_path(
            record["environment_id"], record["product_id"],
            record["target"], record["profile"])
        path.parent.mkdir(parents=True, exist_ok=True)
        self._atomic_write(path, json.dumps(record, ensure_ascii=False, indent=2))

    def get_diagnosis(self, product_id: str, environment_id: str, target: str,
                      profile: str = "default") -> dict | None:
        record = self._read_json(
            self._diagnosis_path(environment_id, product_id, target, profile))
        if record is None:
            return None
        current = self.get_result(product_id, environment_id, target, profile)
        record["stale"] = (
            record["result_updated_at"]
            != (current or {}).get("updated_at")
        )
        return record
