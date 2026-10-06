"""Public deployment workbench API; no product-specific branches."""

import json
from pathlib import Path

import yaml
from fastapi import HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from ..deployment.adoption import discover_existing
from ..deployment.models import AdoptionInput, DiscoveryInput, DraftInput
from ..deployment.probes import probe
from ..deployment.workbench import Workbench, templates
from ..operations import OperationError
from .schemas import public_task


class ImportInput(BaseModel):
    source_yaml: str = Field(max_length=262144)


class ApplyInput(BaseModel):
    acknowledge_change: bool = False


def register(app, settings, store):
    service = Workbench(settings, store)

    def call(function, *args, **kwargs):
        try:
            return function(*args, **kwargs)
        except OperationError:
            raise
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get("/api/v1/deployment/templates")
    def deployment_templates():
        return call(templates, settings)

    @app.post("/api/v1/deployment/discover")
    def discover_installations(item: DiscoveryInput):
        request = item.model_dump()
        ssh = request.pop("ssh") or None
        request["operation"] = "discover"
        return call(probe, item.host, request, ssh=ssh)

    @app.post("/api/v1/deployment/discover-existing")
    def discover_existing_instances(item: AdoptionInput):
        return call(discover_existing, item)

    @app.post("/api/v1/deployment/import-targets")
    def import_targets(item: ImportInput):
        try:
            config = yaml.safe_load(item.source_yaml)
            if not isinstance(config, dict):
                raise ValueError("需要 pgcluster YAML 对象")
            targets = []
            for field, kind, title in (
                ("logical_replications", "logical", "逻辑复制"),
                ("mmr_clusters", "mmr", "多活"),
                ("citus_clusters", "citus", "分布式"),
                ("streaming_clusters", "streaming", "主备"),
            ):
                for name in config.get(field) or {}:
                    targets.append(
                        {"value": kind + "." + name, "label": title + " · " + name}
                    )
            if not targets:
                raise ValueError("文件没有可部署的集群定义")
            return targets
        except (yaml.YAMLError, ValueError, TypeError) as exc:
            raise HTTPException(422, "无法解析部署目标：" + str(exc)) from exc

    @app.get("/api/v1/deployment/drafts")
    def drafts():
        return [
            json.loads(path.read_text())
            for path in sorted(service.drafts.glob("*.json"))
        ]

    @app.post("/api/v1/deployment/drafts", status_code=201)
    def create_draft(item: DraftInput):
        return call(service.save, None, item.spec, item.expected_revision)

    @app.get("/api/v1/deployment/drafts/{draft_id}")
    def draft(draft_id: str):
        return call(service.draft, draft_id)

    @app.get('/api/v1/deployment/drafts/{draft_id}/plans')
    def draft_plans(draft_id: str):
        call(service.draft,draft_id)
        rows=[]
        for path in service.plans.glob('*/plan.json'):
            row=json.loads(path.read_text())
            if row['environment_id']==draft_id:
                rows.append({'id':row['id'],'action':row['action'],'ready':row['ready'],'draft_revision':row['draft_revision'],'target':row['target']})
        return rows

    @app.put("/api/v1/deployment/drafts/{draft_id}")
    def save_draft(draft_id: str, item: DraftInput):
        return call(service.save, draft_id, item.spec, item.expected_revision)

    @app.post("/api/v1/environments/{environment_id}/deployment-draft")
    def import_environment(environment_id: str):
        return call(service.import_current, environment_id)

    @app.post("/api/v1/deployment/drafts/{draft_id}/layout")
    def layout(draft_id: str):
        return call(service.layout, draft_id)

    @app.post("/api/v1/deployment/drafts/{draft_id}/plan")
    def generate(draft_id: str):
        return call(service.generate, draft_id)

    @app.get("/api/v1/deployment/plans/{plan_id}")
    def plan(plan_id: str):
        row = call(service.plan, plan_id)
        attempts = []
        for request in store.deployments.deployment_requests():
            if request["plan_id"] != plan_id or not request.get("task_id"):
                continue
            task = store.tasks.get_task(request["task_id"])
            if task:
                attempts.append(
                    {
                        "task_id": task["id"],
                        "status": task["status"],
                        "created_at": task["created_at"],
                        "finished_at": task.get("finished_at"),
                    }
                )
        row["attempts"] = attempts
        checkpoint = settings.data_dir / 'deployment-checkpoints' / (plan_id+'.json')
        row['checkpoint'] = json.loads(checkpoint.read_text()) if checkpoint.is_file() else None
        environment = store.environments.get_environment(row['environment_id'])
        row['completed'] = bool(environment and environment.get('applied_deployment_plan_id') == plan_id)
        return row

    @app.get("/api/v1/deployment/plans/{plan_id}/files/{name}")
    def plan_file(plan_id: str, name: str):
        row = call(service.plan, plan_id)
        if name not in row["files"]:
            raise HTTPException(404, "计划文件不存在")
        return FileResponse(
            Path(row["config_path"]).parent / name,
            media_type="text/plain",
            filename=name,
            headers={"X-Content-Type-Options": "nosniff"},
        )

    @app.post("/api/v1/deployment/plans/{plan_id}/associate")
    def associate(plan_id: str):
        return call(service.associate, plan_id)

    @app.post("/api/v1/deployment/plans/{plan_id}/apply", status_code=202)
    def apply(plan_id: str, item: ApplyInput):
        return public_task(call(app.state.deployments.apply, plan_id, item.acknowledge_change))
