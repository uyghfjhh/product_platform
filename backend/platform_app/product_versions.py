"""Installed versions and immutable, content-addressed product package artifacts."""

import hashlib
import io
import tarfile

from .diagnostics import _git
from .product_catalog import discover_products

EXCLUDED = {
    ".git",
    "__pycache__",
    "node_modules",
    "output",
    "runtime",
    "logs",
    "dist",
    "legacy",
}


def package_files(root):
    return [
        p
        for p in sorted(root.rglob("*"))
        if p.is_file()
        and not p.is_symlink()
        and not EXCLUDED.intersection(p.relative_to(root).parts)
        and p.suffix not in {".pyc", ".log", ".core"}
        and not p.name.startswith("core.")
    ]


def versions(settings, store):
    rows = []
    for manifest in discover_products(settings.products_root).values():
        revision = _git(manifest.package_root, "rev-parse", "HEAD")
        rows.extend(
            {
                "product_id": manifest.id,
                "title": manifest.title,
                "version": version,
                "source_revision": revision,
                "installed": True,
            }
            for version in manifest.versions
        )
    return {"versions": rows, "artifacts": store.orchestration.list("releases")}


def capture(settings, store, product_id, version):
    manifest = discover_products(settings.products_root).get(product_id)
    if manifest is None or version not in manifest.versions:
        raise ValueError("产品或版本不在安装声明中")
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode="w:gz") as archive:
        for path in package_files(manifest.package_root):
            if path.stat().st_size > 32 * 1024 * 1024:
                raise ValueError("产品文件过大，请先清理运行残留")
            archive.add(
                path, arcname=path.relative_to(manifest.package_root), recursive=False
            )
    content = data.getvalue()
    digest = hashlib.sha256(content).hexdigest()
    identity = digest[:32]
    path = store.backend.root / "product-artifacts" / (identity + ".tar.gz")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".part")
    temporary.write_bytes(content)
    temporary.replace(path)
    return store.orchestration.put(
        "releases",
        {
            "product_id": product_id,
            "version": version,
            "sha256": digest,
            "size": len(content),
            "source_revision": _git(manifest.package_root, "rev-parse", "HEAD"),
        },
        identity,
    )


def artifact(store, identity):
    row = store.orchestration.get("releases", identity)
    if row is None:
        raise KeyError("版本产物不存在")
    path = store.backend.root / "product-artifacts" / (identity + ".tar.gz")
    if (
        not path.is_file()
        or hashlib.sha256(path.read_bytes()).hexdigest() != row["sha256"]
    ):
        raise ValueError("版本产物完整性校验失败")
    return path, row
