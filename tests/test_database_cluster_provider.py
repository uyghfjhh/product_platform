from pathlib import Path

from platform_app.providers import PgclusterDatabaseProvider
from test_api import settings_for


def test_pgcluster_provider_builds_platform_lifecycle_plans(tmp_path):
    settings = settings_for(tmp_path)
    pgcluster = settings.pgcluster_root / "pgcluster"
    pgcluster.parent.mkdir(parents=True)
    pgcluster.write_text("#!/usr/bin/env python3\n", encoding="utf-8")
    config = tmp_path / "profiles" / "cluster.yaml"
    config.parent.mkdir()
    config.write_text("target: test\n", encoding="utf-8")
    environment = {"deployment_config": str(config)}
    provider = PgclusterDatabaseProvider()

    for action in ("deployment.validate", "deployment.create", "deployment.start",
                   "deployment.stop", "deployment.health", "deployment.heal"):
        plan = provider.lifecycle(settings, environment, action, "mmr.test")
        assert plan.config_file == config.resolve()
        assert plan.target == "mmr.test"
        assert plan.action == action
        assert plan.command.cwd == settings.pgcluster_root
        assert str(pgcluster) in plan.command.command


def test_pgcluster_provider_rejects_missing_config(tmp_path):
    settings = settings_for(tmp_path)
    provider = PgclusterDatabaseProvider()
    try:
        provider.lifecycle(settings, {"deployment_config": str(tmp_path / "missing")},
                          "deployment.start", "mmr.test")
    except FileNotFoundError as exc:
        assert "部署配置不存在" in str(exc)
    else:
        raise AssertionError("missing deployment config must be rejected")
