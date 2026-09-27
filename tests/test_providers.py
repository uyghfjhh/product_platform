from pathlib import Path

import pytest
from platform_app.config import Settings
from platform_app.providers import command_for
from products.fbasecman.provider import FbasecmanProvider


def settings_for(tmp_path: Path) -> Settings:
    return Settings(
        data_dir=tmp_path / "data",
        pgcluster_root=tmp_path / "pgcluster",
        fbasecman_regress_root=tmp_path / "cman",
        fbase_regress_root=tmp_path / "fbase",
        license_key_dir=tmp_path / "keys",
        license_vendor="测试厂商",
    )


def test_deployment_provider_builds_confirmation_command(tmp_path):
    settings = settings_for(tmp_path)
    settings.pgcluster_root.mkdir()
    (settings.pgcluster_root / "pgcluster").write_text("", encoding="utf-8")
    config = tmp_path / "cluster.yaml"
    config.write_text("target: demo\n", encoding="utf-8")

    spec = command_for(settings, {
        "product_id": "fbasecman",
        "deployment_config": str(config),
    }, "deployment.clean", "demo", {})

    assert spec.cwd == settings.pgcluster_root
    assert spec.command[-2:] == ["demo", "--yes"]
    assert str(config) in spec.command


def test_fbasecman_provider_keeps_environment_preconditions(tmp_path):
    settings = settings_for(tmp_path)
    with pytest.raises(RuntimeError, match="pgcluster 回归部署方案"):
        command_for(settings, {"product_id": "fbasecman", "id": "lab"},
                    "tests.fbasecman", "guc.case", {})


def test_fbase_provider_validates_cluster_parameter(tmp_path):
    settings = settings_for(tmp_path)
    settings.fbase_regress_root.mkdir()
    (settings.fbase_regress_root / "run.sh").write_text("", encoding="utf-8")
    with pytest.raises(ValueError, match="mac 或 mmr"):
        command_for(settings, {"product_id": "fbase-database"},
                    "tests.fbase", "all", {"cluster": "unknown"})


def test_fbasecman_target_validation_is_owned_by_provider(tmp_path, monkeypatch):
    settings = settings_for(tmp_path)
    monkeypatch.setattr(
        "products.fbasecman.provider.FbasecmanProvider.discover",
        lambda self, current: [{"target": "suite.case"}],
    )
    provider = FbasecmanProvider()
    assert provider.validate_target(settings, "suite.case")
    assert provider.validate_target(settings, "suite")
    assert provider.validate_target(settings, "failed")
    assert not provider.validate_target(settings, "other.case")
