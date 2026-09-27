"""fbasecman deployment and regression HTTP routes.

The platform installs this router only while the product package is present.
Shared environment persistence, topology validation, and task execution remain
platform services; this module owns product-specific request shapes and URLs.
"""

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field

from platform_app.topology import configured_topology
from products.fbasecman.deployment.profile import legacy_root, profile_paths, save_profile
from products.fbasecman.reports.artifacts import (
    case_artifacts,
    case_log,
    export_source_report,
    recent_case_statuses,
)


class ProfileInput(BaseModel):
    mmr1_port: int = Field(default=15011, ge=1024, le=65500)
    # This is a path on the target database host, never the platform checkout.
    data_root: str = Field(default="/home/postgres/fbasecman_regress_v2_mmr")
    license_file: str = Field(default="/home/postgres/license/license.dat")


def create_router(settings, store) -> APIRouter:
    router = APIRouter()

    def product_environment(environment_id: str) -> dict:
        environment = store.get_environment(environment_id)
        if environment is None or environment["product_id"] != "fbasecman":
            raise HTTPException(status_code=404, detail="fbasecman 环境不存在")
        return environment

    @router.get("/api/v1/environments/{environment_id}/fbasecman-profile")
    def profile(environment_id: str):
        product_environment(environment_id)
        deployment, override = profile_paths(settings, environment_id)
        context = legacy_root(settings, environment_id) / "output" / "env" / "test_context.yaml"
        return {
            "generated": deployment.is_file() and override.is_file(),
            "deployment_config": str(deployment),
            "test_override": str(override),
            "context_ready": context.is_file(),
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
        store.update_environment(environment_id, candidate)
        return profile(environment_id)

    @router.get("/api/v1/fbasecman/cases/{target}/artifacts")
    def artifacts(target: str, environment_id: str | None = None):
        if environment_id:
            product_environment(environment_id)
        try:
            return case_artifacts(settings, target, environment_id)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.get("/api/v1/fbasecman/case-statuses")
    def statuses(environment_id: str | None = None):
        if environment_id:
            product_environment(environment_id)
        try:
            return recent_case_statuses(settings, environment_id)
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
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    return router
