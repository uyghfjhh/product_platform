"""Pipelines and schedules reuse the operation service and durable submission keys."""

import time
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from pydantic import BaseModel, Field

from .operations import OperationError, OperationRequest

TERMINAL = {"SUCCEEDED", "FAILED", "CANCELLED", "RECOVERY_REQUIRED"}


class PipelineStep(BaseModel):
    action: str = Field(min_length=1, max_length=100)
    target: str | None = None
    parameters: dict = Field(default_factory=dict)
    deployment_plan_id: str | None = None


class PipelineInput(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    environment_id: str
    steps: list[PipelineStep] = Field(min_length=1, max_length=30)


class ScheduleInput(BaseModel):
    pipeline_id: str
    interval_seconds: int = Field(ge=60, le=31536000)
    enabled: bool = False
    acknowledge_change: bool = False


class Orchestrator:
    def __init__(self, store, operations):
        self.store, self.operations = store, operations
        self._notification_recovery = time.monotonic()

    def start(self, pipeline_id, acknowledged=False, source="manual", run_id=None):
        from .resources import product_lock
        definition=self.store.orchestration.get('pipelines',pipeline_id)
        if definition is None:raise OperationError(404,'流水线不存在')
        environment=self.store.environments.get_environment(definition['environment_id'])
        if environment is None:raise OperationError(404,'流水线环境已不存在')
        with product_lock(self.operations.settings,environment['product_id']):
            return self._start(pipeline_id,acknowledged,source,run_id)

    def _start(self, pipeline_id, acknowledged=False, source="manual", run_id=None):
        definition = self.store.orchestration.get("pipelines", pipeline_id)
        if definition is None:
            raise OperationError(404, "流水线不存在")
        if not acknowledged:
            raise OperationError(422, "请确认执行该流水线的全部步骤")
        if (
            self.store.environments.get_environment(definition["environment_id"])
            is None
        ):
            raise OperationError(404, "流水线环境已不存在")
        run_id = run_id or uuid4().hex
        with self.store.backend.transaction():
            existing = self.store.orchestration.get("runs", run_id)
            if existing:
                return existing
            return self.store.orchestration.put(
                "runs",
                {
                    "pipeline_id": pipeline_id,
                    "definition": definition,
                    "status": "RUNNING",
                    "index": 0,
                    "tasks": [],
                    "acknowledged": True,
                    "source": source,
                },
                run_id,
            )

    def cancel(self, run_id):
        with self.store.backend.transaction():
            row = self.store.orchestration.get("runs", run_id)
            if row is None:
                raise OperationError(404, "流水线执行不存在")
            if row["status"] not in TERMINAL:
                row["status"] = "CANCELLING"
                if row["tasks"]:
                    self.store.tasks.request_cancel(row["tasks"][-1])
                row = self.store.orchestration.put("runs", row, run_id)
            return row

    def tick(self):
        self.runs()
        self.schedules()
        self.notifications()
        from .notifications import deliver

        deliver(self.store.orchestration)

    def runs(self):
        for row in self.store.orchestration.list("runs"):
            if row["status"] not in {"RUNNING", "CANCELLING"}:
                continue
            with self.store.backend.transaction():
                row = self.store.orchestration.get("runs", row["id"])
                if row["status"] not in {"RUNNING", "CANCELLING"}:
                    continue
                index = row["index"]
                task = (
                    self.store.tasks.get_task(row["tasks"][index])
                    if len(row["tasks"]) > index
                    else None
                )
                if task and task["status"] not in TERMINAL:
                    continue
                if row["status"] == "CANCELLING":
                    row["status"] = (
                        "RECOVERY_REQUIRED"
                        if task and task["status"] == "RECOVERY_REQUIRED"
                        else "CANCELLED"
                    )
                elif task:
                    if task["status"] != "SUCCEEDED":
                        row.update(status=task["status"], reason=task.get("reason"))
                    else:
                        row["index"] += 1
                if row["status"] == "RUNNING" and row["index"] == len(
                    row["definition"]["steps"]
                ):
                    row["status"] = "SUCCEEDED"
                if row["status"] == "RUNNING" and len(row["tasks"]) == row["index"]:
                    step = row["definition"]["steps"][row["index"]]
                    try:
                        submitted = self.operations.submit(
                            OperationRequest(
                                environment_id=row["definition"]["environment_id"],
                                **step,
                                acknowledge_change=True,
                                submission_key="pipeline:"
                                + row["id"]
                                + ":"
                                + str(row["index"]),
                            )
                        )
                        row["tasks"].append(submitted["id"])
                    except OperationError as exc:
                        if exc.status_code not in (409, 503):
                            row.update(status="FAILED", reason=str(exc))
                self.store.orchestration.put("runs", row, row["id"])

    def schedules(self):
        now = datetime.now(UTC)
        for item in self.store.orchestration.list("schedules"):
            if not item["enabled"] or datetime.fromisoformat(item["next_run_at"]) > now:
                continue
            with self.store.backend.transaction():
                item = self.store.orchestration.get("schedules", item["id"])
                if (
                    not item["enabled"]
                    or datetime.fromisoformat(item["next_run_at"]) > now
                ):
                    continue
                # Due identity is stable across a crash between creating the
                # run and advancing the schedule. Missed intervals coalesce.
                due = item["next_run_at"]
                import hashlib

                run_id = hashlib.sha256((item["id"] + ":" + due).encode()).hexdigest()[
                    :32
                ]
                active = any(
                    r["pipeline_id"] == item["pipeline_id"]
                    and r["status"] in {"RUNNING", "CANCELLING"}
                    for r in self.store.orchestration.list("runs")
                )
                if not active:
                    try:
                        self.start(
                            item["pipeline_id"], True, "schedule:" + item["id"], run_id
                        )
                    except OperationError as exc:
                        item["error"] = str(exc)
                item["next_run_at"] = (
                    now + timedelta(seconds=item["interval_seconds"])
                ).isoformat()
                self.store.orchestration.put("schedules", item, item["id"])

    def notifications(self):
        if time.monotonic()-self._notification_recovery>60:
            self.store.tasks.recover_notification_outbox()
            self._notification_recovery=time.monotonic()
        for task in self.store.tasks.pending_notifications():
            with self.store.backend.transaction():
                if self.store.orchestration.get("notifications", task["id"]):
                    self.store.tasks.acknowledge_notification(task['id'])
                    continue
                self.store.orchestration.put(
                    "notifications",
                    {
                        "task_id": task["id"],
                        "environment_id": task["environment_id"],
                        "status": task["status"],
                        "action": task["action"],
                        "target": task["target"],
                        "read": False,
                    },
                    task["id"],
                )
                self.store.tasks.acknowledge_notification(task['id'])
