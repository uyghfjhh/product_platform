"""Deployments repository with a shared transaction backend."""
import json
import re
import uuid

from .backend import ACTIVE_STATUSES, ConflictError, now


class DeploymentsStore:
    def __init__(self, backend, owner):
        self.backend, self.owner = backend, owner
        self.root, self.runtime_dir, self.logs_dir = backend.root, backend.runtime_dir, backend.logs_dir

    def deployment_requests(self):
        return [row for p in (self.root / "deployment-requests").glob("*.json")
                if (row := self.backend._read_json(p))]

    def deployment_request(self, request_id):
        if not re.fullmatch(r"[a-f0-9]{32}", request_id):
            raise ValueError("部署申请 ID 无效")
        return self.backend._read_json(self.root / "deployment-requests" / (request_id + ".json"))

    def begin_deployment_request(self, plan_id, environment_id, acknowledged):
        with self.backend._locked():
            for row in self.deployment_requests():
                if row["environment_id"] != environment_id:
                    continue
                task = self.owner.tasks.get_task(row.get("task_id", "")) if row.get("task_id") else None
                pending = row["state"] in {"PENDING", "ASSOCIATED"}
                active = task and task["status"] in ACTIVE_STATUSES
                if pending or active:
                    if row["plan_id"] == plan_id:
                        return row
                    raise ConflictError("环境有待执行部署申请")
            if self.owner.tasks._has_active_task(environment_id):
                raise ConflictError("环境有活动任务")
            row = {"id": uuid.uuid4().hex, "plan_id": plan_id,
                   "environment_id": environment_id, "acknowledged": acknowledged,
                   "state": "PENDING", "task_id": None, "created_at": now()}
            self.backend._atomic_write(self.root / "deployment-requests" / (row["id"] + ".json"),
                               json.dumps(row, ensure_ascii=False))
            return row

    def update_deployment_request(self, request_id, **changes):
        with self.backend._locked():
            row = self.deployment_request(request_id)
            if row is None:
                raise KeyError(request_id)
            row.update(changes, updated_at=now())
            self.backend._atomic_write(self.root / "deployment-requests" / (request_id + ".json"),
                               json.dumps(row, ensure_ascii=False))
            return row

    def has_pending_deployment(self, environment_id, except_request=None):
        return any(row["environment_id"] == environment_id and row["id"] != except_request
                   and row["state"] in {"PENDING", "ASSOCIATED"}
                   for row in self.deployment_requests())
