"""Public deployment workbench API; no product-specific branches."""

import json
from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from ..deployment.models import DiscoveryInput, DraftInput
from ..deployment.probes import probe
from ..deployment.workbench import Workbench, templates
from .schemas import OperationInput


class ApplyInput(BaseModel):
    acknowledge_change: bool = False


def register(app, settings, store):
    service = Workbench(settings, store)

    def call(function, *args):
        try:
            return function(*args)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get("/api/v1/deployment/templates")
    def deployment_templates():
        return call(templates, settings)

    @app.post("/api/v1/deployment/discover")
    def discover_installations(item: DiscoveryInput):
        return call(probe, item.host, {"operation": "discover", **item.model_dump()})

    @app.get("/api/v1/deployment/drafts")
    def drafts():
        return [json.loads(path.read_text()) for path in sorted(service.drafts.glob("*.json"))]

    @app.post("/api/v1/deployment/drafts", status_code=201)
    def create_draft(item: DraftInput):
        return call(service.save, None, item.spec, item.expected_revision)

    @app.get("/api/v1/deployment/drafts/{draft_id}")
    def draft(draft_id: str):
        return call(service.draft, draft_id)

    @app.put("/api/v1/deployment/drafts/{draft_id}")
    def save_draft(draft_id: str, item: DraftInput):
        return call(service.save, draft_id, item.spec, item.expected_revision)

    @app.post("/api/v1/environments/{environment_id}/deployment-draft")
    def import_environment(environment_id: str):
        return call(service.import_current, environment_id)

    @app.post("/api/v1/deployment/drafts/{draft_id}/plan")
    def generate(draft_id: str):
        return call(service.generate, draft_id)

    @app.get("/api/v1/deployment/plans/{plan_id}")
    def plan(plan_id: str):
        return call(service.plan, plan_id)

    @app.get("/api/v1/deployment/plans/{plan_id}/files/{name}")
    def plan_file(plan_id: str, name: str):
        row = call(service.plan, plan_id)
        if name not in row["files"]:
            raise HTTPException(404, "计划文件不存在")
        return FileResponse(Path(row["config_path"]).parent / name, media_type="text/plain",
                            filename=name, headers={"X-Content-Type-Options": "nosniff"})

    @app.post("/api/v1/deployment/plans/{plan_id}/associate")
    def associate(plan_id: str):
        return call(service.associate, plan_id)

    @app.post("/api/v1/deployment/plans/{plan_id}/apply", status_code=202)
    def apply(plan_id: str, item: ApplyInput):
        row = call(service.verify, plan_id)
        if row["action"] == "deployment.create" and not item.acknowledge_change:
            raise HTTPException(422, "请确认此方案将初始化并部署新实例")
        call(service.associate, plan_id)
        return app.state.start_operation(OperationInput(environment_id=row["environment_id"],
            action=row["action"], target=row["target"], acknowledge_change=item.acknowledge_change,
            deployment_plan_id=plan_id, submission_key="deployment-plan:" + plan_id))
