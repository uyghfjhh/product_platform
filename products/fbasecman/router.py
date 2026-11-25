"""fbasecman deployment and regression HTTP routes.

The platform installs this router only while the product package is present.
Shared environment persistence, topology validation, and task execution remain
platform services; this module owns product-specific request shapes and URLs.
"""

import json
import os
import shutil
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field, model_validator

from platform_app.product_catalog import discover_products
from platform_app.topology import configured_topology
from products.fbasecman.deployment.profile import profile_paths, save_profile
from products.fbasecman.reports.artifacts import (
    case_artifacts,
    case_log,
    export_source_report,
    recent_case_statuses,
)


class ProfileInput(BaseModel):
    mmr1_port: int = Field(default=15011, ge=1024, le=65500)
    # This is a path on the target database host, never the platform checkout.
    data_root: str = Field(
        default_factory=lambda: os.environ.get(
            "FBCMAN_DATA_ROOT", "/home/postgres/fbasecman_regress_v2_mmr"))
    license_file: str = Field(
        default_factory=lambda: os.environ.get(
            "FBCMAN_LICENSE_FILE", "/home/postgres/license/license.dat"))


class BuildInput(BaseModel):
    id: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=100)
    fbasecman_bin: str = Field(min_length=1, max_length=4096)
    license_dir: str = Field(min_length=1, max_length=4096)


class TestSettingsInput(BaseModel):
    fbasecman_bin: str | None = Field(default=None, min_length=1, max_length=4096)
    license_dir: str | None = Field(default=None, min_length=1, max_length=4096)
    builds: list[BuildInput] | None = Field(default=None, min_length=1, max_length=50)
    active_build_id: str | None = None

    @model_validator(mode='after')
    def valid_selection(self):
        if self.builds is None:
            if not self.fbasecman_bin or not self.license_dir:
                raise ValueError('请填写可执行文件和 License 目录')
        else:
            ids = [item.id for item in self.builds]
            if len(set(ids)) != len(ids) or self.active_build_id not in ids:
                raise ValueError('版本 ID 必须唯一，且必须选择一个当前版本')
            if any(not item.name.strip() for item in self.builds):
                raise ValueError('版本名称不能为空')
        return self


class MemoryComparisonInput(BaseModel):
    start: str | None = None
    end: str | None = None
    stack: str | None = None
    query: str = Field(default="", max_length=160)
    direction: Literal["growth", "reduction"] = "growth"
    excluded: list[str] = Field(default_factory=list)


def create_router(settings, store) -> APIRouter:
    router = APIRouter()

    def product_environment(environment_id: str) -> dict:
        environment = store.environments.get_environment(environment_id)
        if environment is None or environment["product_id"] != "fbasecman":
            raise HTTPException(status_code=404, detail="fbasecman 环境不存在")
        return environment

    @router.get("/api/v1/environments/{environment_id}/fbasecman-longrun")
    def longrun_catalog(environment_id: str):
        from products.fbasecman.longrun import DEFAULT_SECONDS, TARGET, WORKLOADS, suite_directory
        from products.fbasecman.longrun_memory import tools_for

        product_environment(environment_id)
        issue = None
        try:
            product = discover_products(settings.products_root)["fbasecman"]
            suite_directory(product.source_root)
            deployment, override = profile_paths(settings, environment_id)
            if not deployment.is_file() or not override.is_file():
                issue = "请先准备所绑定的平台 fbasecman 部署环境"
        except (OSError, ValueError) as exc:
            issue = str(exc)
        memory_issue = None
        try:
            tools_for(settings.runtime_dir)
        except ValueError as exc:
            memory_issue = str(exc)
        return {
            "target": TARGET,
            "duration_seconds": DEFAULT_SECONDS,
            "workloads": [{"id": name, "title": title} for name, title in WORKLOADS.items()],
            "issue": issue,
            "memory_issue": memory_issue,
        }

    def memory_directory(environment_id: str, task_id: UUID, *, include_deleted=False):
        from products.fbasecman.longrun import TARGET

        product_environment(environment_id)
        task = store.tasks.get_task(str(task_id))
        if (not task or task["environment_id"] != environment_id or task["target"] != TARGET
                or task["action"] != "stability.fbasecman"):
            raise HTTPException(404, "常稳任务不存在")
        root = settings.artifact_dir("fbasecman", environment_id).resolve()
        run = (root / "runs" / str(task_id)).resolve()
        path = run / "memory"
        if not run.is_relative_to(root) or run != root / "runs" / str(task_id) or path.is_symlink():
            raise HTTPException(404, "内存诊断不存在")
        if not include_deleted and (run / "memory.deleted").exists():
            raise HTTPException(410, "这项内存诊断已删除")
        return path

    @router.get("/api/v1/fbasecman-memory-tasks")
    def memory_tasks(before: UUID | None = None):
        from platform_app.api.schemas import public_task
        from products.fbasecman.longrun import TARGET

        cursor = str(before) if before else None
        results = []
        while True:
            batch = store.tasks.list_tasks(100, before=cursor, include_archived=True)
            for task in batch:
                cursor = task["id"]
                if (task["action"] != "stability.fbasecman" or task["target"] != TARGET
                        or not any(json.loads(task["parameters"]).get(key) for key in ("memory_profile", "asan_build_id"))):
                    continue
                try:
                    memory_directory(task["environment_id"], UUID(cursor))
                except HTTPException as exc:
                    if exc.status_code in (404, 410):
                        continue
                    raise
                results.append(public_task(task))
                if len(results) == 50:
                    return {"tasks": results, "next": cursor}
            if len(batch) < 100:
                return {"tasks": results, "next": None}

    @router.delete("/api/v1/environments/{environment_id}/fbasecman-longrun/{task_id}/memory")
    def delete_memory_report(environment_id: str, task_id: UUID):
        from platform_app.actions import TERMINAL
        from platform_regress.execution.locking import ExclusiveFileLock

        path = memory_directory(environment_id, task_id, include_deleted=True)
        task = store.tasks.get_task(str(task_id))
        if task["status"] not in TERMINAL:
            raise HTTPException(409, "只能删除已结束任务的内存诊断")
        if not any(json.loads(task["parameters"]).get(key) for key in ("memory_profile", "asan_build_id")):
            raise HTTPException(404, "这项任务未开启内存诊断")
        deleted = path.with_name("memory.deleted")
        try:
            with ExclusiveFileLock(path.with_name(".memory-delete.lock"), "删除内存诊断"):
                if deleted.is_symlink() or (deleted.exists() and not deleted.is_dir()):
                    raise HTTPException(409, "内存诊断删除目录异常")
                if path.is_dir() and not deleted.exists():
                    path.rename(deleted)
                else:
                    deleted.mkdir(exist_ok=True)
                for item in deleted.iterdir():
                    if item.is_dir() and not item.is_symlink():
                        shutil.rmtree(item)
                    else:
                        item.unlink()
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc
        return {"deleted": True}

    @router.get("/api/v1/environments/{environment_id}/fbasecman-longrun/{task_id}/memory")
    def memory_report(environment_id: str, task_id: UUID):
        from products.fbasecman.longrun_memory import ARTIFACTS

        path = memory_directory(environment_id, task_id)
        summary = path / "summary.json"
        if not summary.is_file() or summary.resolve().parent != path:
            return {"available": False}
        result = json.loads(summary.read_text(encoding="utf-8"))
        return {**result, "available": True, "files": [
            {"name": name, "title": title} for name, (title, _) in ARTIFACTS.items()
            if (path / name).is_file() and (path / name).resolve().parent == path
        ]}

    @router.get("/api/v1/environments/{environment_id}/fbasecman-longrun/{task_id}/memory-live")
    def memory_live(environment_id: str, task_id: UUID):
        from products.fbasecman.longrun_heap import index, measurement_start, rss_series

        directory = memory_directory(environment_id, task_id)
        task = store.tasks.get_task(str(task_id))
        parameters = json.loads(task["parameters"])
        return {**index(directory), "rss": rss_series(directory),
                "asan": bool(parameters.get("asan_build_id")),
                "measurement_start": measurement_start(directory),
                "enabled": bool(json.loads(task.get("parameters") or "{}").get("memory_profile"))}

    @router.get("/api/v1/environments/{environment_id}/fbasecman-longrun/{task_id}/asan")
    def asan_reports(environment_id: str, task_id: UUID):
        from products.fbasecman.longrun_asan import report_paths, summarize

        directory = memory_directory(environment_id, task_id) / "asan"
        if directory.is_symlink():
            raise HTTPException(404, "ASan 报告不存在")
        build = directory / "build.json"
        return {
            "build": json.loads(build.read_text(encoding="utf-8")) if build.is_file() else None,
            "files": [summarize(path) for path in report_paths(directory)],
        }

    @router.get("/api/v1/environments/{environment_id}/fbasecman-longrun/{task_id}/asan/{name}")
    def asan_report(environment_id: str, task_id: UUID, name: str):
        from products.fbasecman.longrun_asan import report_paths

        directory = memory_directory(environment_id, task_id) / "asan"
        if not directory.is_symlink():
            for path in report_paths(directory):
                if path.name == name:
                    return FileResponse(path, media_type="text/plain", headers={"X-Content-Type-Options": "nosniff"})
        raise HTTPException(404, "ASan 原始报告不存在")

    @router.get("/api/v1/environments/{environment_id}/fbasecman-longrun/{task_id}/memory-analysis")
    def memory_analysis(
        environment_id: str, task_id: UUID, start: str | None = None, end: str | None = None,
        stack: str | None = None, query: str = Query(default="", max_length=160),
        direction: str = Query(default="growth", pattern="^(growth|reduction)$"),
    ):
        from products.fbasecman.longrun_heap import compare

        try:
            return compare(memory_directory(environment_id, task_id), start, end, stack, query, direction)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.post("/api/v1/environments/{environment_id}/fbasecman-longrun/{task_id}/memory-analysis")
    def memory_analysis_filtered(environment_id: str, task_id: UUID, item: MemoryComparisonInput):
        from products.fbasecman.longrun_heap import compare

        try:
            return compare(memory_directory(environment_id, task_id), **item.model_dump())
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.get("/api/v1/environments/{environment_id}/fbasecman-longrun/{task_id}/memory/{name}")
    def memory_artifact(environment_id: str, task_id: UUID, name: str):
        from products.fbasecman.longrun_memory import ARTIFACTS

        directory = memory_directory(environment_id, task_id)
        path = (directory / name).resolve()
        if name not in ARTIFACTS or path.parent != directory or not path.is_file():
            raise HTTPException(404, "内存诊断证据不存在")
        return FileResponse(path, media_type=ARTIFACTS[name][1], headers={
            "X-Content-Type-Options": "nosniff", "Content-Security-Policy": "default-src 'none'",
        })

    @router.get('/api/v1/environments/{environment_id}/fbasecman-test-settings')
    def test_settings(environment_id: str):
        from products.fbasecman.test_settings import describe
        return describe(settings, product_environment(environment_id))

    @router.put('/api/v1/environments/{environment_id}/fbasecman-test-settings')
    def save_test_settings(environment_id: str, item: TestSettingsInput):
        from products.fbasecman.test_settings import resolve
        from platform_app.resources import validate_registration
        environment = product_environment(environment_id)
        candidate = {**environment, 'product_test_settings': item.model_dump(exclude_none=True)}
        try:
            value = resolve(settings, candidate, inspect=True)
        except (ValueError, OSError, TimeoutError) as exc:
            raise HTTPException(422, str(exc)) from exc
        store.environments.update_environment(environment_id, candidate, validator=validate_registration)
        from products.fbasecman.test_settings import describe
        return describe(settings, candidate)

    @router.get("/api/v1/environments/{environment_id}/fbasecman-profile")
    def profile(environment_id: str):
        product_environment(environment_id)
        deployment, override = profile_paths(settings, environment_id)
        context = settings.profile_dir(environment_id) / "fixture" / "test_context.yaml"
        defaults = ProfileInput()
        return {
            "generated": deployment.is_file() and override.is_file(),
            "deployment_config": str(deployment),
            "test_override": str(override),
            "context_ready": context.is_file(),
            "defaults": {
                "data_root": defaults.data_root,
                "license_file": defaults.license_file,
            },
        }

    @router.post("/api/v1/environments/{environment_id}/fbasecman-profile")
    def create_profile(environment_id: str, item: ProfileInput):
        environment = product_environment(environment_id)
        try:
            path, _ = save_profile(settings, environment, **item.model_dump())
            candidate = {
                **environment,
                "deployment_config": str(path),
                "deployment_target": "mmr.fbasecman_regress",
                "port": item.mmr1_port,
            }
            configured_topology(settings, candidate)
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        store.environments.update_environment(environment_id, candidate)
        return profile(environment_id)

    @router.get("/api/v1/fbasecman/cases/{target}/artifacts")
    def artifacts(target: str, environment_id: str | None = None):
        if environment_id:
            product_environment(environment_id)
        try:
            return case_artifacts(settings, target, environment_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.get("/api/v1/fbasecman/case-statuses")
    def statuses(environment_id: str | None = None):
        if environment_id:
            product_environment(environment_id)
        try:
            return recent_case_statuses(settings, environment_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.get("/api/v1/fbasecman/environments/{environment_id}/reports/{format_name}")
    def export(environment_id: str, format_name: str):
        product_environment(environment_id)
        try:
            content = export_source_report(settings, environment_id, format_name)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        filename = "fbasecman-junit.xml" if format_name == "junit" else "fbasecman-report.html"
        media = "application/xml" if format_name == "junit" else "text/html"
        return Response(
            content=content, media_type=media,
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @router.get("/api/v1/fbasecman/cases/{target}/logs")
    def logs(
        target: str,
        filename: str,
        environment_id: str | None = None,
        last_lines: int = Query(default=500, ge=1, le=5000),
    ):
        if environment_id:
            product_environment(environment_id)
        try:
            return case_log(
                settings, target, filename, environment_id=environment_id,
                last_lines=last_lines,
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    return router
