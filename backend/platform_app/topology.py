"""将 pgcluster 的已校验配置转换为前端可绘制的拓扑。"""

import json
import subprocess
import sys
from pathlib import Path

from .config import Settings


TOPOLOGY_SCRIPT = r"""
import json
import sys
from pgclusterlib.config import load
from pgclusterlib.runtime import Runtime

config = load(sys.argv[1])
target = sys.argv[2]
config.validate(target)
runtime = Runtime(config)
names = runtime.target_instances(target)
nodes = []
for name in names:
    instance = config.instance(name)
    nodes.append({
        "id": name,
        "label": name,
        "host": instance["host_config"]["address"],
        "port": instance["port"],
        "data_dir": instance["data_dir"],
        "role": "instance",
    })

edges = []
kind, cluster_name = target.split(".", 1)
streaming_names = [cluster_name] if kind == "streaming" else []
if kind == "logical":
    link = config.logical_replications[cluster_name]
    streaming_names = [link["pub"]["streaming_cluster"], link["sub"]["streaming_cluster"]]
if kind == "citus":
    cluster = config.citus_clusters[cluster_name]
    streaming_names = [cluster["coordinator"]["streaming_cluster"]]
    streaming_names += [item["streaming_cluster"] for item in cluster["workers"].values()]
if kind == "mmr":
    cluster = config.mmr_clusters[cluster_name]
    streaming_names = [item["streaming_cluster"] for item in cluster["members"].values()]

for stream_name in streaming_names:
    stream = config.streaming_clusters[stream_name]
    primary = stream["primary"]
    for node in nodes:
        if node["id"] == primary:
            node["role"] = "primary"
            node["group"] = stream_name
    for standby in stream.get("standbys") or []:
        secondary = standby["instance"]
        edges.append({"id": "stream:" + primary + ":" + secondary, "source": primary,
                      "target": secondary, "kind": "streaming"})
        for node in nodes:
            if node["id"] == secondary:
                node["role"] = "standby"
                node["group"] = stream_name

if kind == "logical":
    left = config.streaming_clusters[streaming_names[0]]["primary"]
    right = config.streaming_clusters[streaming_names[1]]["primary"]
    edges.append({"id": "logical:" + left + ":" + right, "source": left,
                  "target": right, "kind": "logical"})
if kind == "mmr":
    members = [config.streaming_clusters[name]["primary"] for name in streaming_names]
    for index, left in enumerate(members):
        for right in members[index + 1:]:
            edges.append({"id": "mmr:" + left + ":" + right, "source": left,
                          "target": right, "kind": "mmr"})
if kind == "citus":
    coordinator = config.streaming_clusters[streaming_names[0]]["primary"]
    for name in streaming_names[1:]:
        worker = config.streaming_clusters[name]["primary"]
        edges.append({"id": "citus:" + coordinator + ":" + worker, "source": coordinator,
                      "target": worker, "kind": "citus"})

print(json.dumps({"target": target, "kind": kind, "nodes": nodes, "edges": edges}, ensure_ascii=False))
"""


STATUS_SCRIPT = r"""
import json
import sys
from pgclusterlib.config import load
from pgclusterlib.runtime import Runtime

config = load(sys.argv[1])
target = sys.argv[2]
config.validate(target)
print(json.dumps(Runtime(config).status_display(target), ensure_ascii=False))
"""


def configured_topology(settings: Settings, environment: dict) -> dict:
    path = Path(environment.get("deployment_config") or "")
    target = environment.get("deployment_target")
    if not path.is_file() or not target:
        raise ValueError("请先登记有效的 pgcluster 配置和目标")
    result = subprocess.run(
        [sys.executable, "-c", TOPOLOGY_SCRIPT, str(path), target],
        cwd=settings.pgcluster_root,
        capture_output=True,
        text=True,
        timeout=15,
    )
    if result.returncode:
        raise ValueError(result.stderr.strip() or "无法读取部署拓扑")
    return json.loads(result.stdout)


def observed_status(settings: Settings, environment: dict) -> dict:
    path = Path(environment.get("deployment_config") or "")
    target = environment.get("deployment_target")
    if not path.is_file() or not target:
        raise ValueError("请先登记有效的 pgcluster 配置和目标")
    try:
        result = subprocess.run(
            [sys.executable, "-c", STATUS_SCRIPT, str(path), target],
            cwd=settings.pgcluster_root, capture_output=True, text=True, timeout=30,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("读取节点状态超时") from exc
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "读取节点状态失败")
    return json.loads(result.stdout)
