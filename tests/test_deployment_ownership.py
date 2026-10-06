import json
from types import SimpleNamespace

import pytest
import yaml
from platform_app.deployment.ownership import enroll


class Executor:
    def __init__(self, path):
        self.path = path
        self.marker = None
        self.identity = "111"
        self.owner = True

    def exists(self, host, path):
        return path.endswith("/PG_VERSION") or self.marker is not None

    def realpath(self, host, path):
        return str(self.path)

    def run(self, argv, **kwargs):
        return SimpleNamespace(
            returncode=0 if self.owner else 1,
            stdout="Database system identifier: " + self.identity,
        )

    def read_text(self, host, path):
        return self.marker

    def write_text(self, host, path, content):
        self.marker = content


def setup(tmp_path):
    root = tmp_path / "control"
    (root / "environments").mkdir(parents=True)
    path = tmp_path / "configuration.yaml"
    path.write_text("test")
    (root / "environments/owned.yaml").write_text(
        yaml.safe_dump({"deployment_config": str(path)})
    )
    executor = Executor(tmp_path / "database")
    config = SimpleNamespace(
        path=path.resolve(),
        instance=lambda _: {
            "data_dir": str(executor.path),
            "host_config": {"address": "127.0.0.1"},
            "installation_config": {"home": "/opt/pg"},
        },
    )
    runtime = SimpleNamespace(executor=executor, target_instances=lambda _: ["primary"])
    return root, config, runtime


def test_registered_adoption_receipt_guards_database_identity(tmp_path):
    root, config, runtime = setup(tmp_path)
    enroll(root, "owned", config, "streaming.one", runtime)
    assert json.loads(runtime.executor.marker)["system_identifier"] == "111"
    runtime.executor.identity = "222"
    with pytest.raises(ValueError, match="身份已变化"):
        enroll(root, "owned", config, "streaming.one", runtime)
    assert json.loads(runtime.executor.marker)["system_identifier"] == "111"


def test_different_directory_owner_cannot_be_enrolled(tmp_path):
    root, config, runtime = setup(tmp_path)
    runtime.executor.owner = False
    with pytest.raises(ValueError, match="所有者"):
        enroll(root, "owned", config, "streaming.one", runtime)
    assert runtime.executor.marker is None


def test_configuration_must_match_registered_environment(tmp_path):
    root, config, runtime = setup(tmp_path)
    config.path = tmp_path / "other.yaml"
    with pytest.raises(ValueError, match="配置与登记"):
        enroll(root, "owned", config, "streaming.one", runtime)
    assert runtime.executor.marker is None
