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
        return Workbench(settings, store, app.state.orchestrator, app.state.monitoring)

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

    @app.get('/api/v1/workload-runs/{identity}/monitoring')
    def run_monitoring(identity: str):
        from ..workloads.dashboard import dashboard
        return checked(lambda: dashboard(app.state.monitoring, workbench().run(identity), store))

    @app.get('/api/v1/environments/{identity}/workload-monitoring')
    def environment_monitoring(identity: str):
        from datetime import UTC, datetime, timedelta
        from ..monitoring import observation_fingerprint
        from ..workloads.dashboard import dashboard
        from ..workloads.sql import environment_fingerprint
        environment = checked(lambda: workbench().environment(identity))
        try:
            fingerprint = observation_fingerprint(settings, environment)
        except (ValueError, OSError, KeyError):
            fingerprint = None
        now = datetime.now(UTC)
        preview = {'id': None, 'environment_id': identity, 'status': 'RUNNING',
                   'created_at': (now-timedelta(minutes=15)).isoformat(), 'updated_at': now.isoformat(),
                   'definition': {'environment_id': identity, 'environment_fingerprint': environment_fingerprint(environment),
                                  'monitoring_fingerprint': fingerprint}, 'tasks': [], 'task_details': []}
        return checked(lambda: dashboard(app.state.monitoring, preview, store))

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
        value['proxy_monitor'] = (json.loads((path / 'proxy-monitor.json').read_text())
                                  if (path / 'proxy-monitor.json').is_file() else None)
        value['connection_evidence'] = (json.loads((path / 'connection-evidence.json').read_text())
                                        if (path / 'connection-evidence.json').is_file() else None)
        return value
