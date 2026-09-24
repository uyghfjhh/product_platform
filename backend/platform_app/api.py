"""统一 HTTP API；产品命令通过已登记的提供者与本机任务执行。"""

import asyncio
import json
import re
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .actions import ACTIONS, TERMINAL, actions_for_environment
from .catalog import get_product, list_products
from .cman_artifacts import case_artifacts, case_log, export_source_report, recent_case_statuses
from .config import Settings, load_settings
from .database import execute_query, list_columns, list_objects
from .discovery import discover_cases
from .fbasecman_profile import legacy_root, profile_paths, save_profile
from .license import LicenseInput, generate, options
from .storage import ConflictError, Store
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


class QueryInput(BaseModel):
    sql: str = Field(min_length=1, max_length=200000)
    max_rows: int = Field(default=200, ge=1, le=1000)
    port: int | None = Field(default=None, ge=1, le=65535)


class FbasecmanProfileInput(BaseModel):
    mmr1_port: int = Field(default=15011, ge=1024, le=65500)
    data_root: str = Field(default="/home/postgres/product_platform/fbasecman_regress")
    license_file: str = Field(default="/home/postgres/license/license.dat")


def public_task(task: dict) -> dict:
    result = dict(task)
    result["parameters"] = json.loads(result["parameters"])
    result["cancel_requested"] = bool(result["cancel_requested"])
    return result


def create_app(settings: Settings | None = None, enqueuer=None) -> FastAPI:
    settings = settings or load_settings()
    store = Store(settings.database)
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
        return {"status": "ok", "storage": "sqlite", "data_dir": str(settings.data_dir)}

    @app.get("/api/v1/products")
    def products():
        return list_products(settings)

    @app.get("/api/v1/integrations")
    def integrations():
        items = (
            ("pgcluster", settings.pgcluster_root, "pgcluster"),
            ("fbasecman-regress", settings.fbasecman_regress_root, "run.sh"),
            ("fbase-regress", settings.fbase_regress_root, "run.sh"),
        )
        return [
            {"id": item_id, "path": str(root), "available": (root / entry).is_file()}
            for item_id, root, entry in items
        ]

    @app.get("/api/v1/licenses/options")
    def license_options():
        return options(settings)

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

    @app.get("/api/v1/environments/{environment_id}/actions")
    def environment_actions(environment_id: str):
        value = store.get_environment(environment_id)
        if value is None:
            raise HTTPException(status_code=404, detail="环境不存在")
        return actions_for_environment(value)

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

    @app.get("/api/v1/environments/{environment_id}/fbasecman-profile")
    def fbasecman_profile(environment_id: str):
        value = store.get_environment(environment_id)
        if value is None or value["product_id"] != "fbasecman":
            raise HTTPException(status_code=404, detail="fbasecman 环境不存在")
        deployment, override = profile_paths(settings, environment_id)
        context = legacy_root(settings, environment_id) / "output" / "env" / "test_context.yaml"
        return {"generated": deployment.is_file() and override.is_file(),
                "deployment_config": str(deployment), "test_override": str(override),
                "context_ready": context.is_file()}

    @app.post("/api/v1/environments/{environment_id}/fbasecman-profile")
    def create_fbasecman_profile(environment_id: str, item: FbasecmanProfileInput):
        value = store.get_environment(environment_id)
        if value is None or value["product_id"] != "fbasecman":
            raise HTTPException(status_code=404, detail="fbasecman 环境不存在")
        try:
            path, _ = save_profile(settings, value, **item.model_dump())
            candidate = {**value, "deployment_config": str(path),
                         "deployment_target": "mmr.fbasecman_regress", "port": item.mmr1_port}
            configured_topology(settings, candidate)
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        store.update_environment(environment_id, candidate)
        return fbasecman_profile(environment_id)

    @app.get("/api/v1/fbasecman/cases/{target}/artifacts")
    def fbasecman_case_artifacts(target: str, environment_id: str | None = None):
        if environment_id and store.get_environment(environment_id) is None:
            raise HTTPException(status_code=404, detail="环境不存在")
        try:
            return case_artifacts(settings, target, environment_id)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/v1/fbasecman/case-statuses")
    def fbasecman_case_statuses(environment_id: str | None = None):
        if environment_id and store.get_environment(environment_id) is None:
            raise HTTPException(status_code=404, detail="环境不存在")
        try:
            return recent_case_statuses(settings, environment_id)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/v1/fbasecman/environments/{environment_id}/reports/{format_name}")
    def fbasecman_export(environment_id: str, format_name: str):
        environment = store.get_environment(environment_id)
        if environment is None or environment["product_id"] != "fbasecman":
            raise HTTPException(status_code=404, detail="fbasecman 环境不存在")
        try:
            content = export_source_report(settings, environment_id, format_name)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        filename = "fbasecman-junit.xml" if format_name == "junit" else "fbasecman-report.html"
        media = "application/xml" if format_name == "junit" else "text/html"
        return Response(content=content, media_type=media,
                        headers={"Content-Disposition": f'attachment; filename="{filename}"'})

    @app.get("/api/v1/fbasecman/cases/{target}/logs")
    def fbasecman_case_log(
        target: str,
        filename: str,
        environment_id: str | None = None,
        last_lines: int = Query(default=500, ge=1, le=5000),
    ):
        if environment_id and store.get_environment(environment_id) is None:
            raise HTTPException(status_code=404, detail="环境不存在")
        try:
            return case_log(settings, target, filename, environment_id=environment_id,
                            last_lines=last_lines)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

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
            action["id"] for action in actions_for_environment(environment)
        }:
            raise HTTPException(status_code=422, detail="当前环境不支持该操作")
        action = ACTIONS[item.action]
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
            if item.action in {"deployment.failover", "deployment.rejoin"}:
                allowed = {f"streaming.{node['group']}" for node in topology["nodes"] if node.get("group")}
            if target not in allowed:
                raise HTTPException(status_code=422, detail="操作目标不属于当前环境拓扑")
            if item.action in {"deployment.validate", "deployment.create", "deployment.health", "deployment.clean"} and target != environment["deployment_target"]:
                raise HTTPException(status_code=422, detail="该操作只能作用于当前集群")
        if item.action == "tests.fbase" and item.parameters.get("cluster") not in {
            "mac",
            "mmr",
        }:
            raise HTTPException(
                status_code=422, detail="FBase 测试需要 mac 或 mmr 集群"
            )
        if item.action == "tests.fbasecman":
            valid = {case["target"] for case in discover_cases(settings, "fbasecman")}
            valid.update({case.split(".", 1)[0] for case in valid})
            valid.add("failed")
            if target not in valid:
                raise HTTPException(status_code=422, detail="未知 fbasecman 用例或套件")
        task = store.create_task(
            item.environment_id,
            item.action,
            target,
            item.parameters,
            item.submission_key,
        )
        try:
            enqueuer(task["id"])
        except Exception as exc:
            store.transition_task(task["id"], ("QUEUED",), "FAILED", reason="任务入队失败")
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
        path = settings.data_dir / "operations" / (task_id + ".log")
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

    if settings.frontend_dist.is_dir():
        app.mount(
            "/",
            StaticFiles(directory=settings.frontend_dist, html=True),
            name="frontend",
        )

    return app


app = create_app()
