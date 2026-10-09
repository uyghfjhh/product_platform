"""fbasecman deployment and regression HTTP routes.

The platform installs this router only while the product package is present.
Shared environment persistence, topology validation, and task execution remain
platform services; this module owns product-specific request shapes and URLs.
"""

import os

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field, model_validator

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


def create_router(settings, store) -> APIRouter:
    router = APIRouter()

    def product_environment(environment_id: str) -> dict:
        environment = store.environments.get_environment(environment_id)
        if environment is None or environment["product_id"] != "fbasecman":
            raise HTTPException(status_code=404, detail="fbasecman 环境不存在")
        return environment

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
