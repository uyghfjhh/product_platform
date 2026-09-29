"""统一 HTTP API；产品命令通过已登记的提供者与本机任务执行。"""

import asyncio
import json
import os
import re
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import bundle
from .actions import TERMINAL, action_for_environment, actions_for_environment
from .product_catalog import ProductManifestError, discover_products, validate_parameters
from .catalog import get_product, list_products
from .config import Settings, load_settings
from .database import execute_query, list_columns, list_objects
from .discovery import discover_cases, validate_target
from .license import (
    LicenseInput,
    change_key_password,
    delete_key,
    generate,
    generate_key,
    key_metadata,
    options,
    revoke_key,
)
from .product_routes import register_product_routes
from .filestore import ConflictError, FileStore
from .topology import configured_topology, observed_status

IDENTIFIER = r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$"
TARGET = re.compile(r"^[A-Za-z0-9_.,:-]{1,160}$")


class EnvironmentInput(BaseModel):
    id: str = Field(pattern=IDENTIFIER)
    product_id: str
    title: str = Field(min_length=1, max_length=120)
    host: str = Field(min_length=1, max_length=255)
    port: int = Field(ge=1, le=65535)
    database_name: str = "postgres"
    database_user: str = "postgres"
    deployment_config: str | None = None
    deployment_target: str | None = None


class OperationInput(BaseModel):
    environment_id: str
    action: str
    target: str | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)
    submission_key: str | None = Field(default=None, min_length=1, max_length=100)
    acknowledge_change: bool = False


class RegressionBindingInput(BaseModel):
    environment_id: str = Field(pattern=IDENTIFIER)


class QueryInput(BaseModel):
    sql: str = Field(min_length=1, max_length=200000)
    max_rows: int = Field(default=200, ge=1, le=1000)
    port: int | None = Field(default=None, ge=1, le=65535)


class LicenseKeyCreateInput(BaseModel):
    version: str = Field(pattern=r"^1\.[1-9][0-9]*$")
    password: str = Field(min_length=1, max_length=1024)


class LicenseKeyPasswordInput(BaseModel):
    old_password: str = Field(min_length=1, max_length=1024)
    new_password: str = Field(min_length=1, max_length=1024)


class LicenseKeyDeleteInput(BaseModel):
    password: str = Field(min_length=1, max_length=1024)


def public_task(task: dict) -> dict:
    result = dict(task)
    result["parameters"] = json.loads(result["parameters"])
    result["cancel_requested"] = bool(result["cancel_requested"])
    return result


def create_app(settings: Settings | None = None, enqueuer=None) -> FastAPI:
    settings = settings or load_settings()
    store = FileStore(settings.data_dir)
    # Bind existing environments only when a product profile has exactly one
    # compatible deployed topology. Ambiguous choices remain user decisions.
    try:
        installed = discover_products(settings.products_root)
        for manifest in installed.values():
            for profile in manifest.test_profiles:
                if store.get_regression_binding(manifest.id, profile.id):
                    continue
                candidates = [environment for environment in store.list_environments()
                              if environment["product_id"] == manifest.id
                              and profile.accepts(environment["deployment_target"],
                                                  profile.suites[0], profile.id)]
                if len(candidates) == 1:
                    try:
                        store.put_regression_binding(manifest.id, profile.id, candidates[0]["id"])
                    except ConflictError:
                        pass
    except ProductManifestError:
        pass
    if enqueuer is None:
        from .queue import execute

        enqueuer = execute

    app = FastAPI(title="公司产品公共管理平台", version="0.1.0")
    app.state.settings = settings
    app.state.store = store

    @app.exception_handler(ConflictError)
    async def conflict_handler(_request, exc: ConflictError):
        return JSONResponse(
            status_code=409, content={"code": "CONFLICT", "message": str(exc)}
        )

    @app.get("/api/v1/health")
    def health():
        return {
            "status": "ok", "storage": "files",
            "platform_dir": str(settings.platform_dir),
            "environment_dir": str(settings.environment_dir),
        }

    @app.get("/api/v1/products")
    def products():
        return list_products(settings)

    @app.get("/api/v1/integrations")
    def integrations():
        items = [("pgcluster", settings.pgcluster_root, "pgcluster")]
        for product_id in sorted(discover_products(settings.products_root)):
            items.append(
                ("%s-regress" % product_id,
                 settings.product_regress_root(product_id), "regress.yaml")
            )
        return [
            {"id": item_id, "path": str(root), "available": (root / entry).is_file()}
            for item_id, root, entry in items
        ]

    @app.get("/api/v1/diagnostics/availability")
    def diagnostic_availability():
        return {
            "configured": bool(os.environ.get("OPENAI_API_KEY")),
            "model": os.environ.get("PRODUCT_PLATFORM_AI_MODEL", "gpt-6-astra"),
        }

    @app.get("/api/v1/licenses/options")
    def license_options():
        return options(settings)

    @app.get("/api/v1/licenses/keys/{version}")
    def license_key_metadata(version: str):
        try:
            return key_metadata(settings, version)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/v1/licenses/keys")
    def license_key_generate(payload: LicenseKeyCreateInput):
        try:
            return generate_key(settings, payload.version, payload.password)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/api/v1/licenses/keys/{version}/password")
    def license_key_password(version: str, payload: LicenseKeyPasswordInput):
        try:
            return change_key_password(settings, version, payload.old_password, payload.new_password)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.delete("/api/v1/licenses/keys/{version}")
    def license_key_delete(version: str, payload: LicenseKeyDeleteInput):
        try:
            delete_key(settings, version, payload.password)
            return {"deleted": version}
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/api/v1/licenses/keys/{version}/revoke")
    def license_key_revoke(version: str, payload: LicenseKeyDeleteInput):
        try:
            return revoke_key(settings, version, payload.password)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/api/v1/licenses/generate")
    def generate_license(request: LicenseInput):
        try:
            content, license_id = generate(settings, request)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=429, detail=str(exc)) from exc
        return Response(
            content=content,
            media_type="application/octet-stream",
            headers={
                "Content-Disposition": 'attachment; filename="license.dat"',
                "X-License-Id": license_id,
            },
        )

    @app.get("/api/v1/environments")
    def environments():
        return store.list_environments()

    @app.get("/api/v1/regression-bindings")
    def regression_bindings():
        return store.list_regression_bindings()

    @app.put("/api/v1/regression-bindings/{product_id}/{profile_id}")
    def bind_regression(product_id: str, profile_id: str, item: RegressionBindingInput):
        manifest = discover_products(settings.products_root).get(product_id)
        profile = next((value for value in manifest.test_profiles if value.id == profile_id), None) if manifest else None
        environment = store.get_environment(item.environment_id)
        if profile is None or environment is None or environment["product_id"] != product_id:
            raise HTTPException(status_code=422, detail="产品、测试 profile 或环境不匹配")
        if not profile.accepts(environment["deployment_target"], profile.suites[0], profile.id):
            raise HTTPException(status_code=422, detail="环境拓扑不适用于该回归测试")
        return store.put_regression_binding(product_id, profile_id, item.environment_id)

    @app.delete("/api/v1/regression-bindings/{product_id}/{profile_id}")
    def unbind_regression(product_id: str, profile_id: str):
        if not store.delete_regression_binding(product_id, profile_id):
            raise HTTPException(status_code=404, detail="回归绑定不存在")
        return {"status": "ok"}

    @app.post("/api/v1/environments", status_code=201)
    def add_environment(item: EnvironmentInput):
        if not get_product(item.product_id, settings):
            raise HTTPException(status_code=422, detail="未知产品")
        return store.put_environment(item.model_dump())

    @app.get("/api/v1/environments/{environment_id}")
    def environment(environment_id: str):
        value = store.get_environment(environment_id)
        if value is None:
            raise HTTPException(status_code=404, detail="环境不存在")
        return value

    @app.put("/api/v1/environments/{environment_id}")
    def update_environment(environment_id: str, item: EnvironmentInput):
        if item.id != environment_id:
            raise HTTPException(status_code=422, detail="环境 ID 不可修改")
        if not get_product(item.product_id, settings):
            raise HTTPException(status_code=422, detail="未知产品")
        updated = store.update_environment(environment_id, item.model_dump())
        if updated is None:
            raise HTTPException(status_code=404, detail="环境不存在")
        return updated

    @app.delete("/api/v1/environments/{environment_id}")
    def delete_environment(environment_id: str):
        if not store.delete_environment(environment_id):
            raise HTTPException(status_code=404, detail="环境不存在")
        return {"status": "ok", "deleted": environment_id}

    @app.get("/api/v1/environments/{environment_id}/actions")
    def environment_actions(environment_id: str):
        value = store.get_environment(environment_id)
        if value is None:
            raise HTTPException(status_code=404, detail="环境不存在")
        return actions_for_environment(value, settings)

    @app.get("/api/v1/environments/{environment_id}/topology")
    def environment_topology(environment_id: str):
        value = store.get_environment(environment_id)
        if value is None:
            raise HTTPException(status_code=404, detail="环境不存在")
        try:
            return configured_topology(settings, value)
        except (ValueError, FileNotFoundError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/v1/environments/{environment_id}/topology/status")
    def environment_topology_status(environment_id: str):
        value = store.get_environment(environment_id)
        if value is None:
            raise HTTPException(status_code=404, detail="环境不存在")
        try:
            return observed_status(settings, value)
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/v1/cases")
    def cases(product_id: str):
        if not get_product(product_id, settings):
            raise HTTPException(status_code=404, detail="未知产品")
        try:
            return discover_cases(settings, product_id)
        except (FileNotFoundError, RuntimeError) as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @app.post("/api/v1/environments/{environment_id}/query")
    def query_database(environment_id: str, item: QueryInput):
        environment = store.get_environment(environment_id)
        if environment is None:
            raise HTTPException(status_code=404, detail="环境不存在")
        target_env = dict(environment)
        if item.port:
            target_env["port"] = item.port
        try:
            return execute_query(target_env, item.sql, item.max_rows)
        except Exception as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/v1/environments/{environment_id}/objects")
    def database_objects(environment_id: str):
        environment = store.get_environment(environment_id)
        if environment is None:
            raise HTTPException(status_code=404, detail="环境不存在")
        try:
            return list_objects(environment)
        except Exception as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/v1/environments/{environment_id}/objects/{schema}/{table}/columns")
    def database_columns(environment_id: str, schema: str, table: str):
        environment = store.get_environment(environment_id)
        if environment is None:
            raise HTTPException(status_code=404, detail="环境不存在")
        try:
            return list_columns(environment, schema, table)
        except Exception as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/v1/environments/{environment_id}/configuration")
    def environment_configuration(environment_id: str):
        environment = store.get_environment(environment_id)
        if environment is None:
            raise HTTPException(status_code=404, detail="环境不存在")
        config_path = environment["deployment_config"]
        if not config_path:
            raise HTTPException(status_code=404, detail="当前环境未关联部署配置")
        path = Path(config_path)
        if not path.is_file():
            raise HTTPException(status_code=404, detail="配置文件不存在")
        return {
            "path": str(path),
            "content": path.read_text(encoding="utf-8"),
            "language": "yaml",
        }

    @app.post("/api/v1/operations", status_code=202)
    def start_operation(item: OperationInput):
        environment = store.get_environment(item.environment_id)
        if environment is None:
            raise HTTPException(status_code=404, detail="环境不存在")
        if item.action not in {
            action["id"] for action in actions_for_environment(environment, settings)
        }:
            raise HTTPException(status_code=422, detail="当前环境不支持该操作")
        action = action_for_environment(settings, environment, item.action)
        if action is None:
            raise HTTPException(status_code=422, detail="当前环境不支持该操作")
        try:
            manifest = discover_products(settings.products_root).get(environment["product_id"])
            declared = next(
                (value for value in (manifest.actions if manifest else ()) if value.id == item.action),
                None,
            )
            if declared is not None:
                validate_parameters(declared, item.parameters)
        except ProductManifestError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if action.changes_environment and not item.acknowledge_change:
            raise HTTPException(status_code=422, detail="请确认本次操作会修改环境")
        target = item.target or (
            environment["deployment_target"]
            if item.action.startswith("deployment.")
            else environment["id"]
        )
        if not target or not TARGET.fullmatch(target):
            raise HTTPException(status_code=422, detail="目标名称无效")
        if item.action.startswith("deployment."):
            try:
                topology = configured_topology(settings, environment)
            except (ValueError, FileNotFoundError) as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            nodes = {node["id"] for node in topology["nodes"]}
            allowed = {environment["deployment_target"]} | nodes
            if item.action in {"deployment.failover", "deployment.switchover",
                               "deployment.rejoin", "deployment.lag"}:
                allowed = {f"streaming.{node['group']}" for node in topology["nodes"] if node.get("group")}
            if item.action == "deployment.verify":
                allowed = {environment["deployment_target"]} | {
                    f"streaming.{node['group']}" for node in topology["nodes"] if node.get("group")}
            if target not in allowed:
                raise HTTPException(status_code=422, detail="操作目标不属于当前环境拓扑")
            if item.action in {"deployment.validate", "deployment.create", "deployment.health", "deployment.clean"} and target != environment["deployment_target"]:
                raise HTTPException(status_code=422, detail="该操作只能作用于当前集群")
        if declared is not None and declared.validate_target:
            if not validate_target(settings, environment["product_id"], target):
                raise HTTPException(status_code=422, detail="未知产品测试目标")
        if declared is not None and declared.capability == "tests" and manifest.test_profiles:
            requested_profile = item.parameters.get("cluster")
            matches = [profile for profile in manifest.test_profiles
                       if profile.accepts(environment["deployment_target"], target,
                                          requested_profile)]
            if not any((binding := store.get_regression_binding(manifest.id, profile.id))
                       and binding["environment_id"] == environment["id"] for profile in matches):
                raise HTTPException(status_code=422, detail="测试目标未绑定当前产品环境或测试 profile")
        if item.action == "diagnostics.analyze" and store.get_result(
            environment["product_id"], environment["id"], target,
            item.parameters.get("profile", "default"),
        ) is None:
            raise HTTPException(status_code=404, detail="当前环境没有该测试目标的结果")
        task, created = store.create_task_once(
            item.environment_id,
            item.action,
            target,
            item.parameters,
            item.submission_key,
        )
        if created:
            try:
                enqueuer(task["id"])
            except Exception as exc:
                store.finish_task(task["id"], ("QUEUED",), "FAILED", "任务入队失败")
                raise HTTPException(status_code=503, detail="任务入队失败") from exc
        return public_task(task)

    @app.get("/api/v1/operations")
    def operations(limit: int = Query(default=40, ge=1, le=200)):
        return [public_task(task) for task in store.list_tasks(limit)]

    @app.get("/api/v1/operations/{task_id}")
    def operation(task_id: str):
        task = store.get_task(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="任务不存在")
        return public_task(task)

    @app.get("/api/v1/operations/{task_id}/events")
    def operation_events(task_id: str, after: int = Query(default=0, ge=0)):
        if store.get_task(task_id) is None:
            raise HTTPException(status_code=404, detail="任务不存在")
        return store.list_events(task_id, after)

    @app.get("/api/v1/operations/{task_id}/events/stream")
    async def event_stream(task_id: str, after: int = Query(default=0, ge=0)):
        if store.get_task(task_id) is None:
            raise HTTPException(status_code=404, detail="任务不存在")

        async def stream():
            cursor = after
            while True:
                events = store.list_events(task_id, cursor)
                for event in events:
                    cursor = event["sequence"]
                    yield "id: %s\nevent: update\ndata: %s\n\n" % (
                        cursor,
                        json.dumps(event, ensure_ascii=False),
                    )
                task = store.get_task(task_id)
                if task is None or task["status"] in TERMINAL:
                    return
                await asyncio.sleep(0.5)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store"},
        )

    @app.post("/api/v1/operations/{task_id}/cancel")
    def cancel_operation(task_id: str):
        if store.get_task(task_id) is None:
            raise HTTPException(status_code=404, detail="任务不存在")
        if not store.request_cancel(task_id):
            raise HTTPException(status_code=409, detail="任务已经结束或正在取消")
        return public_task(store.get_task(task_id))

    @app.get("/api/v1/operations/{task_id}/log")
    def operation_log(
        task_id: str, last_lines: int = Query(default=500, ge=1, le=5000)
    ):
        task = store.get_task(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="任务不存在")
        path = settings.platform_dir / "operations" / (task_id + ".log")
        if not path.is_file():
            return {"lines": [], "path": str(path), "available": False}
        # 日志按后缀行数读取，前端保留原文与等级高亮的独立表示。
        from collections import deque

        with path.open("r", encoding="utf-8", errors="replace") as handle:
            lines = list(deque(handle, maxlen=last_lines))
        return {
            "lines": [line.rstrip("\n") for line in lines],
            "path": str(path),
            "available": True,
        }

    @app.get("/api/v1/environments/{environment_id}/results")
    def results(environment_id: str):
        if store.get_environment(environment_id) is None:
            raise HTTPException(status_code=404, detail="环境不存在")
        return store.list_results(environment_id)

    def archived_result(environment_id: str, target: str, profile: str) -> tuple[Path, dict]:
        environment = store.get_environment(environment_id)
        if environment is None:
            raise HTTPException(status_code=404, detail="环境不存在")
        if not re.fullmatch(r"[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+", target):
            raise HTTPException(status_code=404, detail="结果不存在")
        result = store.get_result(environment["product_id"], environment_id, target, profile)
        base = (settings.environment_dir / "regression" / environment_id / target).resolve()
        if result is None or Path(result["artifact_dir"]).resolve() != base:
            raise HTTPException(status_code=404, detail="平台归档结果不存在")
        try:
            payload = json.loads((base / "result.json").read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=404, detail="平台归档结果不存在") from exc
        if payload.get("target") != target or not isinstance(payload.get("evidence"), list):
            raise HTTPException(status_code=404, detail="平台归档结果无效")
        return base, payload

    @app.get("/api/v1/environments/{environment_id}/results/{target}/evidence")
    def result_evidence(environment_id: str, target: str, profile: str = "default"):
        _, payload = archived_result(environment_id, target, profile)
        return {"target": target, "execution_id": payload.get("execution_id"),
                "verdict": payload.get("verdict"), "evidence": payload["evidence"]}

    @app.get("/api/v1/environments/{environment_id}/results/{target}/evidence/{reference:path}")
    def result_evidence_file(environment_id: str, target: str, reference: str,
                             profile: str = "default"):
        base, payload = archived_result(environment_id, target, profile)
        if reference not in payload["evidence"]:
            raise HTTPException(status_code=404, detail="证据不存在")
        candidate = (base / reference).resolve()
        if not candidate.is_relative_to(base) or not candidate.is_file():
            raise HTTPException(status_code=404, detail="证据不存在")
        return FileResponse(candidate, media_type="application/octet-stream",
                            filename=candidate.name,
                            headers={"X-Content-Type-Options": "nosniff"})

    @app.get("/api/v1/environments/{environment_id}/results/{target}/bundle")
    def result_bundle(environment_id: str, target: str):
        if not re.fullmatch(r"[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+", target):
            raise HTTPException(status_code=404, detail="结果不存在")
        try:
            payload = bundle.build_bug_bundle(settings, store, environment_id, target)
        except KeyError:
            raise HTTPException(status_code=404, detail="环境不存在") from None
        filename = "bundle-%s-%s.zip" % (environment_id, target)
        return Response(payload, media_type="application/zip", headers={
            "Content-Disposition": 'attachment; filename="%s"' % filename})

    @app.get("/api/v1/environments/{environment_id}/results-bundle")
    def environment_bundle(environment_id: str):
        try:
            payload = bundle.build_bug_bundle(settings, store, environment_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="环境不存在") from None
        filename = "bundle-%s.zip" % environment_id
        return Response(payload, media_type="application/zip", headers={
            "Content-Disposition": 'attachment; filename="%s"' % filename})

    @app.get("/api/v1/environments/{environment_id}/flaky")
    def flaky(environment_id: str, window: int = Query(default=5, ge=2, le=20)):
        if store.get_environment(environment_id) is None:
            raise HTTPException(status_code=404, detail="环境不存在")
        return bundle.flaky_summary(settings, environment_id, window)

    @app.get("/api/v1/environments/{environment_id}/diagnostics/{target}")
    def diagnosis(environment_id: str, target: str, profile: str = "default"):
        environment = store.get_environment(environment_id)
        if environment is None:
            raise HTTPException(status_code=404, detail="环境不存在")
        value = store.get_diagnosis(environment["product_id"], environment_id, target, profile)
        if value is None:
            raise HTTPException(status_code=404, detail="尚无 AI 诊断")
        return value

    register_product_routes(app, settings, store)

    if settings.frontend_dist.is_dir():
        app.mount(
            "/",
            StaticFiles(directory=settings.frontend_dist, html=True),
            name="frontend",
        )

    return app


app = create_app()
