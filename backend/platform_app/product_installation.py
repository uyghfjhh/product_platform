"""Reviewed product package activation; database data and environments are preserved."""

import hashlib
import json
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path

from .config import scope
from .filestore import ConflictError
from .product_catalog import load_manifest
from .product_versions import artifact
from .providers import registry_for
from .resources import frontend_build_lock, product_lock


class ProductInstallationError(RuntimeError):
    pass


def prepare(settings, store, identity):
    archive, row = artifact(store, identity)
    root = store.root / "product-installation-plans" / identity
    staging = root / scope(row["product_id"])
    existing = root / "plan.json"
    if existing.is_file() and staging.is_dir():
        plan = json.loads(existing.read_text())
        verify_package(staging, plan["package_files"])
        return plan
    if not staging.exists():
        root.mkdir(parents=True, exist_ok=True)
        temporary_root = Path(tempfile.mkdtemp(prefix="package-", dir=root))
        temporary = temporary_root / row["product_id"]
        temporary.mkdir()
        try:
            with tarfile.open(archive, "r:gz") as source:
                for member in source.getmembers():
                    path = Path(member.name)
                    if (
                        path.is_absolute()
                        or ".." in path.parts
                        or member.issym()
                        or member.islnk()
                        or not (member.isfile() or member.isdir())
                    ):
                        raise ValueError("产品包包含不安全路径或文件类型")
                source.extractall(temporary, filter="data")
            manifest = load_manifest(temporary)
            if (
                manifest.id != row["product_id"]
                or row["version"] not in manifest.versions
            ):
                raise ValueError("产品包声明与产物目录不一致")
            temporary.replace(staging)
        finally:
            shutil.rmtree(temporary_root)
    manifest = load_manifest(staging)
    plan = {
        "id": identity,
        "product_id": manifest.id,
        "version": row["version"],
        "sha256": row["sha256"],
        "plugin_api": manifest.plugin_api,
        "files": sum(p.is_file() for p in staging.rglob("*")),
        "changes": [
            "替换该产品包代码",
            "重新构建平台前端",
            "刷新产品 Provider；保留数据库、环境、密钥与结果",
        ],
        "status": "READY",
    }
    plan["package_files"] = {
        str(path.relative_to(staging)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in staging.rglob("*")
        if path.is_file()
    }
    store.backend.write_control_file(
        root / "plan.json", json.dumps(plan, ensure_ascii=False)
    )
    return plan


def verify_package(root, expected):
    paths = {
        str(path.relative_to(root)): path for path in root.rglob("*") if path.is_file()
    }
    if set(paths) != set(expected) or any(
        path.is_symlink()
        or hashlib.sha256(path.read_bytes()).hexdigest() != expected[name]
        for name, path in paths.items()
    ):
        raise ValueError("安装计划代码已变化，请重新生成并审阅产物")


def activate(settings, store, identity, build=None):
    plan = prepare(settings, store, identity)
    product_id = plan["product_id"]
    source = store.root / "product-installation-plans" / identity / product_id
    target = settings.products_root / product_id
    with product_lock(settings, product_id, exclusive=True), frontend_build_lock(settings):
        verify_package(source, plan["package_files"])
        environments = [
            e
            for e in store.environments.list_environments()
            if e["product_id"] == product_id
        ]
        if any(
            store.tasks.has_active_task(e["id"])
            or store.deployments.has_pending_deployment(e["id"])
            for e in environments
        ):
            raise ConflictError("产品有活动任务或待执行部署申请")
        if any(
            r["status"] in {"RUNNING", "CANCELLING"}
            and r["definition"]["environment_id"] in {e["id"] for e in environments}
            for r in store.orchestration.list("runs")
        ):
            raise ConflictError("产品有运行中的流水线")
        temporary = Path(
            tempfile.mkdtemp(prefix=".product-install-", dir=settings.products_root)
        )
        replacement = temporary / "new"
        backup = temporary / "previous"
        try:
            shutil.copytree(source, replacement)
            if target.exists():
                target.replace(backup)
            replacement.replace(target)
            try:
                registry_for(settings).refresh()
                registry_for(settings).entry(product_id)
                if build:
                    build()
                else:
                    frontend = settings.products_root.parent / "frontend"
                    built = temporary / "frontend-dist"
                    subprocess.run(
                        [
                            "npm",
                            "--prefix",
                            str(frontend),
                            "run",
                            "build",
                            "--",
                            "--outDir",
                            str(built),
                            "--emptyOutDir",
                        ],
                        check=True,
                        capture_output=True,
                        text=True,
                        timeout=180,
                    )
                    live = frontend / "dist"
                    if live.exists():
                        live.replace(temporary / "previous-dist")
                    built.replace(live)
            except Exception as exc:
                shutil.rmtree(target)
                if backup.exists():
                    backup.replace(target)
                previous_dist = temporary / "previous-dist"
                if previous_dist.exists():
                    live = settings.products_root.parent / "frontend" / "dist"
                    if live.exists():
                        shutil.rmtree(live)
                    previous_dist.replace(live)
                registry_for(settings).refresh()
                raise ProductInstallationError(
                    "产品包安装失败，原包已恢复：" + type(exc).__name__
                ) from exc
            plan["status"] = "APPLIED"
            store.backend.write_control_file(
                store.root / "product-installation-plans" / identity / "plan.json",
                json.dumps(plan, ensure_ascii=False),
            )
            with store.backend.transaction():
                for release in store.orchestration.list('releases'):
                    if release['product_id']==product_id:
                        store.orchestration.put('releases',{**release,'installed':release['id']==identity},release['id'])
            return plan
        finally:
            shutil.rmtree(temporary)
