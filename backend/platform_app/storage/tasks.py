"""Tasks repository with a shared transaction backend."""
import fcntl
import json
import os
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ..file_pages import reverse_lines
from .backend import ACTIVE_STATUSES, TERMINAL_STATUSES, ConflictError, now


class TasksStore:
    EVENT_SEGMENT_SIZE = 1000
    def __init__(self, backend, owner):
        self.backend, self.owner = backend, owner
        self.root, self.runtime_dir, self.logs_dir = backend.root, backend.runtime_dir, backend.logs_dir

    def _task_dir(self, task_id: str) -> Path:
        active = self.root / "tasks" / task_id
        archived = self.root / "archived-tasks" / task_id
        return archived if not active.exists() and archived.is_dir() else active

    def _task_meta(self, task_id: str) -> Path:
        return self._task_dir(task_id) / "meta.json"

    def _task_events(self, task_id: str, sequence: int = 1) -> Path:
        segment = max(0, sequence - 1) // self.EVENT_SEGMENT_SIZE
        name = f"events-{segment:08d}.jsonl" if segment else "events.jsonl"
        return self._task_dir(task_id) / name

    def _write_task(self, task: dict) -> None:
        path = self._task_meta(task["id"])
        path.parent.mkdir(parents=True, exist_ok=True)
        created = not path.exists()
        self.backend._atomic_write(
            path, json.dumps(task, ensure_ascii=False, indent=2, sort_keys=True)
        )
        if created:
            self._index_task(task)
        self._project_notification(task)

    def _project_notification(self, task):
        if task['status'] not in TERMINAL_STATUSES or task.get('notification_recorded'):
            return
        try:
            self.backend.write_control_file(self.root/'notification-outbox'/(task['id']+'.json'), json.dumps({'task_id':task['id']}))
        except OSError:
            # The terminal task is the durable source. Startup/periodic
            # projection recovery can repair this optional delivery index.
            pass

    def recover_notification_outbox(self):
        with self.backend._locked():
            for directory in ('tasks','archived-tasks'):
                for path in (self.root/directory).glob('*/meta.json'):
                    task=self.backend._read_json(path)
                    if task:self._project_notification(task)

    def pending_notifications(self):
        for path in (self.root/'notification-outbox').glob('*.json'):
            task=self.get_task(path.stem)
            if task and task['status'] in TERMINAL_STATUSES:
                yield task

    def acknowledge_notification(self, task_id):
        with self.backend._locked():
            task=self.get_task(task_id)
            if not task or task['status'] not in TERMINAL_STATUSES:
                return
            task['notification_recorded']=True
            self._write_task(task)
            (self.root/'notification-outbox'/(task_id+'.json')).unlink(missing_ok=True)

    def _index_task(self, task):
        index = self.root / "task-index.jsonl"
        with index.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"id": task["id"], "created_at": task["created_at"]}) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        self.backend._sync_directory(self.root)
        if task.get("submission_key"):
            path = self.root / "submissions" / (self.backend._key(task["submission_key"]) + ".json")
            self.backend._atomic_write(path, json.dumps({"task_id": task["id"]}))

    def recover_task_index(self):
        """Rebuild a disposable projection once at runtime startup, including crash gaps."""
        with self.backend._locked():
            rows = self._task_rows()
            rows.sort(key=lambda row: (row.get("created_at", ""), row["id"]))
            self.backend._atomic_write(self.root / "task-index.jsonl", "".join(
                json.dumps({"id": row["id"], "created_at": row["created_at"]}) + "\n"
                for row in rows))
            for row in rows:
                if row.get("submission_key"):
                    path = self.root / "submissions" / (self.backend._key(row["submission_key"]) + ".json")
                    self.backend._atomic_write(path, json.dumps({"task_id": row["id"]}))

    def _task_rows(self, include_archived=True) -> list[dict]:
        rows = []
        tasks_dir = self.root / "tasks"
        paths = list(tasks_dir.glob("*/meta.json"))
        if include_archived:
            paths += list((self.root / "archived-tasks").glob("*/meta.json"))
        for meta in paths:
            row = self.backend._read_json(meta)
            if row:
                rows.append(row)
        return rows

    def _has_active_task(self, environment_id: str) -> bool:
        return any(
            row.get("environment_id") == environment_id
            and row.get("status") in ACTIVE_STATUSES
            for row in self._task_rows(include_archived=False)
        )

    def has_active_task(self, environment_id):
        return self._has_active_task(environment_id)

    def task_for_submission(self, submission_key):
        record = self.backend._read_json(self.root / "submissions" / (self.backend._key(submission_key) + ".json"))
        return self.get_task(record["task_id"]) if record else None

    def record_dispatch(self, task_id, *, pending, error=None):
        with self.backend._locked():
            task = self.get_task(task_id)
            if task:
                task["dispatch_pending"] = pending and task["status"] == "QUEUED"
                task["dispatch_error"] = error
                if not pending:
                    task["last_dispatched_at"] = now()
                self._write_task(task)

    def create_task(self, environment_id: str, action: str, target: str,
                    parameters: dict, submission_key: str | None) -> dict:
        task, _created = self.create_task_once(
            environment_id, action, target, parameters, submission_key)
        return task

    def create_task_once(self, environment_id: str, action: str, target: str,
                         parameters: dict,
                         submission_key: str | None) -> tuple[dict, bool]:
        payload = json.dumps(parameters, ensure_ascii=False, sort_keys=True)
        with self.backend._locked():
            if submission_key:
                row = self.task_for_submission(submission_key)
                if row:
                    if (row["environment_id"] != environment_id
                            or row["action"] != action or row["target"] != target
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
                "dispatch_pending": True,
            }
            self._write_task(task)
        return task, True

    def get_task(self, task_id: str) -> dict | None:
        row = self.backend._read_json(self._task_meta(task_id))
        # Archive moves are atomic, but a reader may have selected the old
        # path just before the move. Retry the stable UUID in the archive.
        return row or self.backend._read_json(self.root / "archived-tasks" / task_id / "meta.json")

    def list_tasks(self, limit: int = 40, *, before: str | None = None, include_archived=False, environment_id=None):
        rows, seen = [], set()
        past_cursor = before is None
        for line in reverse_lines(self.root / "task-index.jsonl"):
            try:
                item = json.loads(line)
            except (ValueError, TypeError):
                continue
            task_id = item["id"]
            if not past_cursor:
                if task_id == before:
                    past_cursor = True
                continue
            if task_id in seen:
                continue
            seen.add(task_id)
            if not include_archived and (self.root / "archived-tasks" / task_id).exists():
                continue
            row = self.get_task(task_id)
            if row and (environment_id is None or row.get("environment_id") == environment_id):
                rows.append(row)
                if len(rows) >= limit:
                    break
        return rows

    def archive_tasks(self, older_than_days=30):
        """Move terminal tasks only; UUID lookup, evidence and idempotency remain valid."""
        if older_than_days < 1:
            raise ValueError("归档至少保留一天")
        cutoff = (datetime.now(UTC) - timedelta(days=older_than_days)).isoformat()
        archived = []
        with self.backend._locked():
            for path in (self.root / "tasks").glob("*/meta.json"):
                row = self.backend._read_json(path)
                if not row or row["status"] not in TERMINAL_STATUSES:
                    continue
                if not row.get("finished_at") or row["finished_at"] >= cutoff or row.get("pending_events"):
                    continue
                os.replace(path.parent, self.root / "archived-tasks" / row["id"])
                archived.append(row["id"])
            self.backend._sync_directory(self.root / "tasks")
            self.backend._sync_directory(self.root / "archived-tasks")
        return archived

    def unfinished_tasks(self) -> list[dict]:
        rows = [row for row in self._task_rows(include_archived=False)
                if row.get("status") in ACTIVE_STATUSES]
        rows.sort(key=lambda item: item.get("created_at") or "")
        return rows

    def transition_task(self, task_id: str, expected: tuple[str, ...],
                        status: str, *, reason: str | None = None,
                        process_id: int | None = None) -> bool:
        with self.backend._locked():
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
            if status in TERMINAL_STATUSES:
                task["last_sequence"] = task.get("last_sequence", 0) + 1
                self._commit_event(task, "operation.finished",
                                   {"status": status, "reason": task.get("reason")}, task["finished_at"])
            else:
                self._write_task(task)
        return True

    def set_progress(self, task_id: str, done: int, total: int, label: str) -> None:
        """Persist suite progress parsed from the runner's [n/m] output lines."""
        with self.backend._locked():
            task = self.get_task(task_id)
            if not task or task["status"] not in {"QUEUED", "RUNNING", "CANCELLING"}:
                return
            task["progress"] = {"done": done, "total": total, "label": label}
            self._write_task(task)

    def request_cancel(self, task_id: str) -> bool:
        with self.backend._locked():
            task = self.get_task(task_id)
            if not task or task["status"] not in {"QUEUED", "RUNNING"}:
                return False
            if task["status"] == "QUEUED":
                task["status"] = "CANCELLED"
                task["finished_at"] = now()
            else:
                task["status"] = "CANCELLING"
            task["cancel_requested"] = 1
            task["last_sequence"] = task.get("last_sequence", 0) + 1
            self._commit_event(task, "operation.cancel_requested", {})
        return True

    def finish_task(self, task_id: str, expected: tuple[str, ...], status: str,
                    reason: str | None) -> bool:
        """Atomically commit terminal state and its durable event in task metadata."""
        if status not in TERMINAL_STATUSES:
            raise ValueError("任务终态无效")
        with self.backend._locked():
            task = self.get_task(task_id)
            if task is None or task["status"] not in expected:
                return False
            at = now()
            task["status"] = status
            task["reason"] = reason
            task["finished_at"] = at
            task["last_sequence"] = task.get("last_sequence", 0) + 1
            self._commit_event(task, "operation.finished", {"status": status, "reason": reason}, at)
        return True

    def _commit_event(self, task: dict, event_type: str, payload: dict, at=None) -> dict:
        event = {"task_id": task["id"], "sequence": task["last_sequence"],
                 "recorded_at": at or now(), "event_type": event_type, "payload": payload}
        retry = bool(task.get("pending_events"))
        task.setdefault("pending_events", []).append(event)
        # This single atomic file is the authoritative commit of state + event.
        # JSONL is a projection; readers merge pending facts until it catches up.
        self._write_task(task)
        self._flush_pending_events(task, check_existing=retry)
        return event

    def _flush_pending_events(self, task: dict, *, check_existing=True) -> None:
        pending = task.get("pending_events") or []
        if not pending:
            return
        try:
            projected = set()
            paths = {self._task_events(task["id"], event["sequence"]) for event in pending}
            if check_existing:
                for path in paths:
                    if not path.is_file():
                        continue
                    raw = path.read_bytes()
                    if raw and not raw.endswith(b"\n"):
                        raw = raw[:raw.rfind(b"\n") + 1]
                        with path.open("wb") as stream:
                            stream.write(raw)
                            stream.flush()
                            os.fsync(stream.fileno())
                    for line in raw.splitlines():
                        try:
                            projected.add(json.loads(line)["sequence"])
                        except (ValueError, KeyError):
                            continue
            for event in pending:
                if event["sequence"] not in projected:
                    ref = {"id": task["id"], "last_sequence": event["sequence"]}
                    self._append_event(ref, event["event_type"], event["payload"], event["recorded_at"])
                    projected.add(event["sequence"])
            clean = dict(task)
            clean.pop("pending_events", None)
            self._write_task(clean)
            task.pop("pending_events", None)
        except OSError:
            # The commit already succeeded. Preserve its outbox; API readers
            # see it and a later append/restart can retry projection safely.
            return

    def recover_event_projections(self) -> None:
        with self.backend._locked():
            for path in (self.root / "tasks").glob("*/meta.json"):
                task = self.backend._read_json(path)
                if task and task.get("pending_events"):
                    self._flush_pending_events(task)

    def _append_event(self, task: dict, event_type: str, payload: dict,
                      at: str | None = None) -> dict:
        event = {
            "task_id": task["id"],
            "sequence": task["last_sequence"],
            "recorded_at": at or now(),
            "event_type": event_type,
            "payload": payload,
        }
        events_file = self._task_events(task["id"], task["last_sequence"])
        events_file.parent.mkdir(parents=True, exist_ok=True)
        with open(events_file, "a", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                handle.write(json.dumps(event, ensure_ascii=False) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
                self.backend._sync_directory(events_file.parent)
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        return event

    def add_event(self, task_id: str, event_type: str, payload: dict) -> dict:
        with self.backend._locked():
            task = self.get_task(task_id)
            if task is None:
                raise KeyError(task_id)
            task["last_sequence"] = task.get("last_sequence", 0) + 1
            event = self._commit_event(task, event_type, payload)
        return {
            "task_id": task_id,
            "sequence": event["sequence"],
            "recorded_at": event["recorded_at"],
            "event_type": event_type,
            "payload": payload,
        }

    def _read_events_since(self, path: Path, after: int) -> dict[int, dict]:
        """Parse events.jsonl; when ``after`` is given, scan backwards from the
        tail and stop at the first event already covered — sequences are
        monotonically increasing so nothing earlier can qualify."""
        events: dict[int, dict] = {}
        if not path.is_file():
            return events
        for line in reverse_lines(path):
            try:
                event = json.loads(line)
            except ValueError:
                continue
            seq = event.get("sequence") if isinstance(event, dict) else None
            if not isinstance(seq, int):
                continue
            if seq <= after:
                break
            if seq not in events:
                events[seq] = event
        return events

    def list_events(self, task_id: str, after: int = 0) -> list[dict]:
        with self.backend._locked():
            events = {}
            paths = list(self._task_dir(task_id).glob("events*.jsonl"))
            def segment(path):
                return 0 if path.name == "events.jsonl" else int(path.stem.split("-")[1])
            for path in sorted(paths, key=segment, reverse=True):
                number = segment(path)
                if number and (number + 1) * self.EVENT_SEGMENT_SIZE <= after:
                    break
                events.update(self._read_events_since(path, after))
            task = self.get_task(task_id) or {}
            events.update({event["sequence"]: event for event in task.get("pending_events", [])})
            return [events[key] for key in sorted(events) if key > after]
