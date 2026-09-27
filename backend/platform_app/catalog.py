"""Expose the installed product package catalog to the HTTP layer."""

from .config import Settings
from .product_catalog import ProductManifestError, discover_products


def list_products(settings: Settings) -> list[dict]:
    """Only installed product packages provide executable capabilities."""
    try:
        manifests = discover_products(settings.products_root)
    except ProductManifestError as exc:
        raise RuntimeError(f"产品目录校验失败: {exc}") from exc
    result = []
    for manifest in manifests.values():
        result.append({
            "id": manifest.id,
            "title": manifest.title,
            "description": "",
            "capabilities": sorted(manifest.capabilities),
            "test_profiles": [
                {"id": profile.id, "title": profile.title,
                 "suites": list(profile.suites),
                 "deployment_targets": list(profile.deployment_targets)}
                for profile in manifest.test_profiles
            ],
            "actions": [
                {"id": item.id, "title": item.title, "capability": item.capability,
                 "changes_environment": item.changes_environment}
                for item in manifest.actions
            ],
            "source_path": manifest.source_path,
            "provider": str(manifest.provider_path) if manifest.provider_path else None,
            "versions": list(manifest.versions),
            "cli": {name: str(path) for name, path in manifest.cli.items()},
            "license": (
                {"product_code": manifest.license.product_code,
                 "allowed_versions": list(manifest.license.allowed_versions)}
                if manifest.license else None
            ),
        })
    return sorted(result, key=lambda item: item["id"])


def get_product(product_id: str, settings: Settings) -> dict | None:
    return next(
        (item for item in list_products(settings) if item["id"] == product_id), None
    )
