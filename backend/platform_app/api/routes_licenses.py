"""License management routes."""

from urllib.parse import quote

from fastapi import HTTPException
from fastapi.responses import Response

from ..config import Settings
from ..license import (
    LicenseInput,
    change_key_password,
    delete_key,
    generate,
    generate_key,
    key_metadata,
    options,
    save_generated_license,
)
from ..license_defaults import (
    DefaultKeyInput,
    LicenseDefaults,
    save_defaults,
    set_default_key,
)
from .schemas import (
    LicenseKeyCreateInput,
    LicenseKeyDeleteInput,
    LicenseKeyPasswordInput,
)


def register(app, settings: Settings) -> None:

    @app.get("/api/v1/licenses/options")
    def license_options():
        return options(settings)

    @app.get("/api/v1/licenses/defaults")
    def license_defaults():
        return options(settings)['defaults']

    @app.put("/api/v1/licenses/defaults")
    def license_defaults_save(payload: LicenseDefaults):
        try:
            return save_defaults(settings, payload).model_dump()
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

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

    @app.put("/api/v1/licenses/default-key")
    def license_default_key(payload: DefaultKeyInput):
        try:
            return set_default_key(settings, payload.version)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/api/v1/licenses/generate")
    def generate_license(request: LicenseInput):
        try:
            content, license_id = generate(settings, request)
            saved_path = save_generated_license(settings, content, request.output_directory) if request.save_to_directory else None
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except OSError as exc:
            raise HTTPException(status_code=422, detail=f"License 保存失败，请检查目录权限：{exc}") from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=429, detail=str(exc)) from exc
        return Response(
            content=content,
            media_type="application/octet-stream",
            headers={
                "Content-Disposition": 'attachment; filename="license.dat"',
                "X-License-Id": license_id,
                **({"X-License-Saved-Path": quote(str(saved_path), safe="/")} if saved_path else {}),
            },
        )
