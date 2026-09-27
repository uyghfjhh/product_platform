import json
import os
from pathlib import Path

import yaml

from framework.errors import SafetyError


MARKER_FILE = ".fbase_regress_v2.json"


class StateStore:
    def __init__(self, root, cluster):
        self.directory = Path(root) / "output" / "envs" / cluster
        self.path = self.directory / "state.yaml"

    def load(self):
        if not self.path.is_file():
            return {"state": "-", "nodes": {}}
        with self.path.open("r", encoding="utf-8") as stream:
            return yaml.safe_load(stream) or {"state": "-", "nodes": {}}

    def save(self, state):
        self.directory.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        with temporary.open("w", encoding="utf-8") as stream:
            yaml.safe_dump(state, stream, default_flow_style=False)
        os.replace(str(temporary), str(self.path))

    def remove(self):
        if self.path.exists():
            self.path.unlink()


def marker_path(data_dir):
    return Path(data_dir) / MARKER_FILE


def write_marker(data_dir, cluster, node, env_id):
    payload = {"cluster": cluster, "node": node, "env_id": env_id}
    path = marker_path(data_dir)
    with path.open("w", encoding="utf-8") as stream:
        json.dump(payload, stream, ensure_ascii=False, sort_keys=True)


def require_marker(data_dir, cluster, node, env_id):
    path = marker_path(data_dir)
    if not path.is_file():
        raise SafetyError("拒绝操作没有平台 marker 的数据库目录: %s" % data_dir)
    try:
        with path.open("r", encoding="utf-8") as stream:
            actual = json.load(stream)
    except (ValueError, OSError) as exc:
        raise SafetyError("数据库目录 marker 无效: %s (%s)" % (path, exc))
    expected = {"cluster": cluster, "node": node, "env_id": env_id}
    if actual != expected:
        raise SafetyError("数据库目录 marker 不匹配: %s" % path)
    return actual
