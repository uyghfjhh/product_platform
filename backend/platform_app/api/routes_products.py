"""Product versions, immutable artifacts and grounded knowledge access."""

import hashlib
import re

from fastapi import HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from ..diagnostics import AIUnavailable, _redact
from ..knowledge import KnowledgeIndexInput, KnowledgeRequest, answer, index, search
from ..product_installation import ProductInstallationError, activate, prepare
from ..product_versions import artifact, capture, versions


class ArtifactInput(BaseModel):
    product_id: str
    version: str


class InstallationInput(BaseModel):
    acknowledge_change: bool = False


def register(app, settings, store):
    def call(function, *args):
        try:
            return function(*args)
        except AIUnavailable as exc:
            raise HTTPException(503, str(exc)) from exc
        except (ValueError, OSError) as exc:
            raise HTTPException(422, str(exc)) from exc
        except ProductInstallationError as exc:
            raise HTTPException(422,str(exc)) from exc
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.get("/api/v1/product-versions")
    def list_versions():
        return versions(settings, store)

    @app.post("/api/v1/product-artifacts", status_code=201)
    def capture_artifact(item: ArtifactInput):
        return call(capture, settings, store, item.product_id, item.version)

    @app.get("/api/v1/product-artifacts/{identity}/download")
    def download_artifact(identity: str):
        path, row = call(artifact, store, identity)
        return FileResponse(
            path,
            filename=row["product_id"] + "-" + row["version"] + ".tar.gz",
            media_type="application/gzip",
        )

    @app.get('/api/v1/product-artifacts/{identity}/installation-plan')
    def installation_plan(identity:str):return call(prepare,settings,store,identity)

    @app.post('/api/v1/product-artifacts/{identity}/activate')
    def activate_product(identity:str,item:InstallationInput):
        if not item.acknowledge_change:raise HTTPException(422,'请审阅并确认产品包替换与前端重建')
        return call(activate,settings,store,identity)

    @app.post("/api/v1/products/{product_id}/knowledge/index")
    def index_knowledge(product_id: str, item: KnowledgeIndexInput):
        return call(index, settings, store, product_id, item.version)

    @app.post("/api/v1/products/{product_id}/knowledge/search")
    def search_knowledge(product_id: str, item: KnowledgeRequest):
        return call(search, store, product_id, item)

    @app.post("/api/v1/products/{product_id}/knowledge/answer")
    def answer_knowledge(product_id: str, item: KnowledgeRequest):
        return call(answer, store, product_id, item)

    @app.get("/api/v1/failure-clusters")
    def failure_clusters():
        groups = {}
        for environment in store.environments.list_environments():
            for result in store.results.list_results(environment["id"]):
                if result["status"] not in {
                    "FAIL",
                    "FAILED",
                    "ERROR",
                    "BLOCKED",
                    "RECOVERY_REQUIRED",
                }:
                    continue
                reason = _redact(result.get("reason") or result["status"])
                normalized = re.sub(r"\b\d+\b", "?", reason)
                identity = hashlib.sha256(
                    (result["product_id"] + normalized).encode()
                ).hexdigest()[:24]
                group = groups.setdefault(
                    identity,
                    {
                        "id": identity,
                        "product_id": result["product_id"],
                        "reason_pattern": normalized,
                        "results": [],
                    },
                )
                group["results"].append(result)
        return sorted(
            groups.values(), key=lambda row: len(row["results"]), reverse=True
        )
