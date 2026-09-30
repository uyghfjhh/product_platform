"""Result, evidence, bundle, flaky and diagnosis routes."""

import json
import re
from datetime import datetime
from pathlib import Path

from fastapi import HTTPException, Query
from fastapi.responses import FileResponse, Response

from .. import bundle
from ..config import Settings

_TARGET_PATTERN = r"[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+"


def _platform_result_index(settings: Settings, environment_id: str) -> dict[str, tuple[Path, float]]:
    """Latest platform result.json per target (single-run and suite-run dirs)."""
    root = settings.output_dir / "regression" / environment_id
    index: dict[str, tuple[Path, float]] = {}
    if not root.is_dir():
        return index
    for path in root.rglob("result.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        target = payload.get("target")
        if payload.get("schema_version") != "1.0" or not isinstance(target, str):
            continue
        mtime = path.stat().st_mtime
        previous = index.get(target)
        if previous is None or mtime > previous[1]:
            index[target] = (path.parent, mtime)
    return index


def _row_timestamp(row: dict) -> float:
    value = row.get("updated_at")
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return datetime.fromisoformat(
            str(value).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return 0.0


def _archived_result(settings: Settings, store, environment_id: str,
                     target: str, profile: str) -> tuple[Path, dict]:
    environment = store.get_environment(environment_id)
    if environment is None:
        raise HTTPException(status_code=404, detail="环境不存在")
    if not re.fullmatch(_TARGET_PATTERN, target):
        raise HTTPException(status_code=404, detail="结果不存在")
    result = store.get_result(environment["product_id"], environment_id, target, profile)
    output_root = settings.output_dir.resolve()
    base: Path | None = None
    if result is not None:
        recorded = Path(result["artifact_dir"]).resolve()
        if recorded.is_relative_to(output_root) and (recorded / "result.json").is_file():
            base = recorded
    if base is None:
        entry = _platform_result_index(settings, environment_id).get(target)
        if entry is not None:
            base = entry[0].resolve()
    if base is None:
        raise HTTPException(status_code=404, detail="平台归档结果不存在")
    try:
        payload = json.loads((base / "result.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=404, detail="平台归档结果不存在") from exc
    if payload.get("target") != target or not isinstance(payload.get("evidence"), list):
        raise HTTPException(status_code=404, detail="平台归档结果无效")
    return base, payload


def register(app, settings: Settings, store) -> None:

    @app.get("/api/v1/environments/{environment_id}/results")
    def results(environment_id: str):
        if store.get_environment(environment_id) is None:
            raise HTTPException(status_code=404, detail="环境不存在")
        rows = store.list_results(environment_id)
        environment = store.get_environment(environment_id)
        for target, (base, mtime) in _platform_result_index(settings, environment_id).items():
            try:
                payload = json.loads((base / "result.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            fresh = {
                "product_id": environment["product_id"],
                "environment_id": environment_id,
                "target": target, "profile": "default",
                "status": payload.get("verdict", "ERROR"),
                "reason": payload.get("reason"),
                "artifact_dir": str(base),
                "updated_at": mtime,
            }
            stale = next((row for row in rows if row["target"] == target), None)
            if stale is None:
                rows.append(fresh)
            elif mtime > _row_timestamp(stale):
                # A store row predates the platform archive (e.g. CLI reruns
                # after a web-run failure) — the newer fact wins the row.
                stale.update(fresh)
        return rows

    @app.get("/api/v1/environments/{environment_id}/results/{target}/evidence")
    def result_evidence(environment_id: str, target: str, profile: str = "default"):
        _, payload = _archived_result(settings, store, environment_id, target, profile)
        return {"target": target, "execution_id": payload.get("execution_id"),
                "verdict": payload.get("verdict"), "evidence": payload["evidence"]}

    @app.get("/api/v1/environments/{environment_id}/results/{target}/evidence/{reference:path}")
    def result_evidence_file(environment_id: str, target: str, reference: str,
                             profile: str = "default"):
        base, payload = _archived_result(settings, store, environment_id, target, profile)
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
        if not re.fullmatch(_TARGET_PATTERN, target):
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
