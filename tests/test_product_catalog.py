from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from platform_app.product_catalog import ProductManifestError, discover_products, load_manifest, validate_parameters
from platform_app.api import create_app
from platform_app.actions import run_task
from platform_app.config import Settings
from platform_app.providers import command_for
from test_api import settings_for


def test_discovers_product_manifest(tmp_path: Path):
    package = tmp_path / "demo"
    package.mkdir()
    (package / "product.yaml").write_text(
        """id: demo\ntitle: Demo\nplugin_api: v1\nregression_sdk: '2'\nversions:\n  - id: '1.0'\ncapabilities:\n  tests: demo-tests\nlicense:\n  product_code: demo\n""",
        encoding="utf-8",
    )
    found = discover_products(tmp_path)
    assert found["demo"].capabilities == {"tests": "demo-tests"}
    assert found["demo"].license.product_code == "demo"
    assert found["demo"].license.default_version == "1.0"


def test_rejects_invalid_manifest(tmp_path: Path):
    package = tmp_path / "demo"
    package.mkdir()
    (package / "product.yaml").write_text("id: Demo\ntitle: Demo\nplugin_api: v1\nregression_sdk: '2'\n", encoding="utf-8")
    with pytest.raises(ProductManifestError, match="id must match"):
        load_manifest(package)


def test_cli_entry_must_stay_inside_product_package(tmp_path: Path):
    package = tmp_path / "demo"
    package.mkdir()
    (package / "run.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    (package / "product.yaml").write_text(
        """id: demo\ntitle: Demo\nplugin_api: v1\nregression_sdk: '2'\ncli:\n  run: run.sh\n""",
        encoding="utf-8",
    )
    assert load_manifest(package).cli["run"].as_posix() == "run.sh"

    (package / "product.yaml").write_text(
        """id: demo\ntitle: Demo\nplugin_api: v1\nregression_sdk: '2'\ncli:\n  run: ../run.sh\n""",
        encoding="utf-8",
    )
    with pytest.raises(ProductManifestError, match="relative product path"):
        load_manifest(package)


def test_discovers_checked_in_fbasecman_package():
    root = Path(__file__).parents[1] / "products"
    found = discover_products(root)
    assert found["fbasecman"].license.product_code == "fbasecman"
    assert {item.id for item in found["fbasecman"].actions} >= {
        "database.check", "tests.fbasecman", "stability.fbasecman",
    }


def test_license_products_do_not_create_platform_products():
    root = Path(__file__).parents[1] / "products"
    found = discover_products(root)
    assert not {"fmmr", "fbase_db_ent", "fd_logical"}.intersection(found)
    assert {item.product_code for item in found["fbase-database"].license.additional_products} == {
        "fmmr", "fbase_db_ent", "fd_logical",
    }


def test_manifest_action_parameter_schema():
    product = discover_products(Path(__file__).parents[1] / "products")["fbase-database"]
    action = next(item for item in product.actions if item.id == "tests.fbase")
    with pytest.raises(ProductManifestError, match="缺少参数"):
        validate_parameters(action, {})
    with pytest.raises(ProductManifestError, match="值无效"):
        validate_parameters(action, {"cluster": "unknown"})
    validate_parameters(action, {"cluster": "mmr"})


def test_removed_package_disables_new_work_but_preserves_environment(tmp_path, monkeypatch):
    package = tmp_path / "products" / "demo"
    package.mkdir(parents=True)
    manifest = package / "product.yaml"
    manifest.write_text(
        "id: demo\ntitle: Demo\nplugin_api: v1\nregression_sdk: '2'\ncapabilities:\n  database: demo\n"
        "actions:\n  - id: database.check\n    title: Check database\n    capability: database\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(Settings, "products_root", property(lambda self: package.parent))
    client = TestClient(create_app(settings_for(tmp_path), enqueuer=lambda task_id: None))
    payload = {"id": "demo-env", "product_id": "demo", "title": "Demo", "host": "127.0.0.1", "port": 5432}
    assert client.post("/api/v1/environments", json=payload).status_code == 201
    assert [item["id"] for item in client.get("/api/v1/products").json()] == ["demo"]

    manifest.unlink()
    assert client.get("/api/v1/products").json() == []
    assert client.get("/api/v1/environments/demo-env").status_code == 200
    assert client.get("/api/v1/environments/demo-env/actions").json() == []
    assert client.post("/api/v1/environments", json={**payload, "id": "new-env"}).status_code == 422
    assert client.post("/api/v1/operations", json={"environment_id": "demo-env", "action": "database.check"}).status_code == 422


def test_rejects_duplicate_license_codes(tmp_path):
    for name in ("first", "second"):
        package = tmp_path / name
        package.mkdir()
        (package / "product.yaml").write_text(
            f"id: {name}\ntitle: {name}\nplugin_api: v1\nregression_sdk: '2'\n"
            "license:\n  product_code: shared\n  allowed_versions: ['1.0']\n",
            encoding="utf-8",
        )
    with pytest.raises(ProductManifestError, match="duplicate license product code"):
        discover_products(tmp_path)


def test_new_product_provider_loads_from_its_directory(tmp_path, monkeypatch):
    package = tmp_path / "products" / "demo"
    package.mkdir(parents=True)
    manifest = package / "product.yaml"
    manifest.write_text(
        "id: demo\ntitle: Demo\nplugin_api: v1\nregression_sdk: '2'\ncapabilities:\n  tests: demo\n"
        "actions:\n  - id: tests.demo\n    title: Run demo\n    capability: tests\n",
        encoding="utf-8",
    )
    (package / "provider.py").write_text(
        "from platform_app.providers import CommandSpec\n"
        "class DemoProvider:\n"
        "    def command(self, settings, environment, action, target, parameters):\n"
        "        return CommandSpec(['true', target], settings.data_dir)\n"
        "    def discover(self, settings): return []\n"
        "    def publish_result(self, store, settings, environment, task, terminal, reason): return terminal, reason\n"
        "    def observe_database(self, environment): return []\n"
        "    def observe_runtime(self, settings, environment): return []\n"
        "    def validate_target(self, settings, target): return True\n"
        "PROVIDER = DemoProvider()\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(Settings, "products_root", property(lambda self: package.parent))
    settings = settings_for(tmp_path)
    spec = command_for(settings, {"product_id": "demo"}, "tests.demo", "case-one", {})
    assert spec.command == ["true", "case-one"]
    manifest.unlink()
    with pytest.raises(ValueError, match="产品未安装"):
        command_for(settings, {"product_id": "demo"}, "tests.demo", "case-one", {})


def test_removed_product_cannot_execute_queued_deployment(tmp_path, monkeypatch):
    package = tmp_path / "products" / "demo"
    package.mkdir(parents=True)
    (package / "product.yaml").write_text(
        "id: demo\ntitle: Demo\nplugin_api: v1\nregression_sdk: '2'\ncapabilities:\n  deployment: database-cluster\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(Settings, "products_root", property(lambda self: package.parent))
    settings = settings_for(tmp_path)
    (package / "product.yaml").unlink()
    with pytest.raises(ValueError, match="产品未安装"):
        command_for(settings, {"product_id": "demo"}, "deployment.start", "cluster", {})


def test_product_router_is_registered_only_while_installed(tmp_path, monkeypatch):
    package = tmp_path / "products" / "demo"
    package.mkdir(parents=True)
    manifest = package / "product.yaml"
    manifest.write_text("id: demo\ntitle: Demo\nplugin_api: v1\nregression_sdk: '2'\n", encoding="utf-8")
    (package / "router.py").write_text(
        "from fastapi import APIRouter\n"
        "def create_router(settings, store):\n"
        "    router = APIRouter()\n"
        "    @router.get('/api/v1/products/demo/check')\n"
        "    def check(): return {'product': 'demo'}\n"
        "    return router\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(Settings, "products_root", property(lambda self: package.parent))
    settings = settings_for(tmp_path)
    installed = TestClient(create_app(settings, enqueuer=lambda task_id: None))
    assert installed.get("/api/v1/products/demo/check").json() == {"product": "demo"}

    manifest.unlink()
    removed = TestClient(create_app(settings, enqueuer=lambda task_id: None))
    assert removed.get("/api/v1/products/demo/check").status_code == 404


def test_new_product_action_runs_through_api_and_worker(tmp_path, monkeypatch):
    package = tmp_path / "products" / "demo"
    package.mkdir(parents=True)
    (package / "product.yaml").write_text(
        "id: demo\ntitle: Demo\nplugin_api: v1\nregression_sdk: '2'\ncapabilities:\n  tests: demo\n"
        "actions:\n  - id: tests.demo\n    title: Run demo\n"
        "    capability: tests\n    changes_environment: true\n",
        encoding="utf-8",
    )
    (package / "provider.py").write_text(
        "from platform_app.providers import CommandSpec\n"
        "class DemoProvider:\n"
        "    def command(self, settings, environment, action, target, parameters):\n"
        "        return CommandSpec(['true'], settings.data_dir)\n"
        "    def discover(self, settings): return []\n"
        "    def publish_result(self, store, settings, environment, task, terminal, reason): return terminal, reason\n"
        "    def observe_database(self, environment): return []\n"
        "    def observe_runtime(self, settings, environment): return []\n"
        "    def validate_target(self, settings, target): return True\n"
        "PROVIDER = DemoProvider()\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(Settings, "products_root", property(lambda self: package.parent))
    settings = settings_for(tmp_path)
    queued = []
    app = create_app(settings, enqueuer=queued.append)
    client = TestClient(app)
    client.post("/api/v1/environments", json={
        "id": "demo-env", "product_id": "demo", "title": "Demo",
        "host": "127.0.0.1", "port": 5432,
    }).raise_for_status()
    response = client.post("/api/v1/operations", json={
        "environment_id": "demo-env", "action": "tests.demo",
        "target": "sample", "acknowledge_change": True,
    })
    assert response.status_code == 202, response.text
    assert queued == [response.json()["id"]]
    run_task(app.state.store, settings, queued[0])
    assert app.state.store.tasks.get_task(queued[0])["status"] == "SUCCEEDED"
