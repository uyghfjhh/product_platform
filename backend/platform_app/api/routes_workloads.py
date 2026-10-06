"""Live workload facts are scoped to the operation's registered environment."""

import json
from uuid import UUID

from fastapi import HTTPException


def register(app, settings, store):
    from pydantic import BaseModel
    from ..workloads.workbench import PlanInput, Workbench

    class Submission(BaseModel):
        acknowledge_change: bool = False

    def workbench():
        return Workbench(settings, store, app.state.orchestrator)

    def checked(action):
        try:
            return action()
        except (ValueError, OSError) as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get('/api/v1/environments/{identity}/workloads')
    def catalog(identity: str):
        return checked(lambda: workbench().catalog(identity))

    @app.post('/api/v1/workload-plans')
    def preview(payload: PlanInput):
        return checked(lambda: workbench().preview(payload))

    @app.post('/api/v1/workload-plans/{identity}/run')
    def start(identity: str, payload: Submission):
        return checked(lambda: workbench().start(identity, payload.acknowledge_change))

    @app.get('/api/v1/workload-runs/{identity}')
    def run(identity: str):
        return checked(lambda: workbench().run(identity))

    @app.post('/api/v1/workload-runs/{identity}/cancel')
    def cancel(identity: str):
        checked(lambda: workbench().run(identity))
        return app.state.orchestrator.cancel(identity)

    @app.get("/api/v1/operations/{identity}/workload")
    def metrics(identity: str):
        try:
            UUID(identity)
        except ValueError as exc:
            raise HTTPException(422, "执行标识无效") from exc
        task = store.tasks.get_task(identity)
        if not task or task["action"] not in {"workload.pgbench", "workload.jdbc"}:
            raise HTTPException(404, "工作负载不存在")
        environment = store.environments.get_environment(task["environment_id"])
        if environment is None:
            raise HTTPException(404, "环境不存在")
        path = (
            settings.artifact_dir(environment["product_id"], environment["id"])
            / "runs"
            / identity
            / "workload"
        )
        value = (
            json.loads((path / "metrics.json").read_text())
            if (path / "metrics.json").is_file()
            else {"samples": [], "summary": {}}
        )
        value["available"] = (path / "metrics.json").is_file()
        value["status"] = task["status"]
        value["reason"] = task.get("reason")
        value["result"] = (
            json.loads((path / "result.json").read_text())
            if (path / "result.json").is_file()
            else None
        )
        return value
