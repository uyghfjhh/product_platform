"""pgcluster 部署驱动：拓扑与运行状态的子进程探针。

这是 ``platform_app.topology`` 的默认 ``pgcluster`` 驱动实现——
平台的部署引擎知识（pgclusterlib API、streaming/logical/citus/mmr
复制模型的角色与边推导）集中在此模块，``topology.py`` 只做驱动分发。
新产品接入其他部署引擎时在产品包内提供同构模块（暴露
``topology(settings, environment)`` / ``status(settings, environment)``），
并通过环境记录的 ``deployment_driver`` 字段指到该模块路径。
"""

import json
import subprocess
import sys
from pathlib import Path

from .config import ROOT, Settings

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

# 节点元数据的“已安装插件”取自实例 catalog（pg_extension）。同一条
# 流复制链上的成员 catalog 一致，每个复制组只查一个可连通的成员
# 即可；整组不可达时返回空集，前端回退到配置声明的扩展集。
def installed_extensions(member_names, database):
    for member in member_names:
        instance = config.instance(member)
        host = "local" if runtime.executor.is_local(instance["host_config"]["address"]) \
            else instance["host_config"]["address"]
        try:
            proc = runtime.executor.run(
                ["env", "PGCONNECT_TIMEOUT=3",
                 runtime._bin(member, "psql"), "-X", "-v", "ON_ERROR_STOP=1",
                 "-h", instance["host_config"]["address"], "-p", str(instance["port"]),
                 "-U", "postgres", "-d", database, "-At",
                 "-c", "SELECT extname FROM pg_extension ORDER BY 1"],
                host=host, check=False)
            names = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
            if proc.returncode == 0 and names:
                return names
        except Exception:
            continue
    return []

nodes = []
for name in names:
    instance = config.instance(name)
    raw_cfg = getattr(config, "raw", {})
    inst_exts = set()
    preloads = raw_cfg.get("postgresql_config", {}).get("parameters", {}).get("shared_preload_libraries") or []
    if isinstance(preloads, str):
        preloads = [x.strip() for x in preloads.split(",")]
    inst_exts.update(preloads)
    plugins = raw_cfg.get("postgresql_installations", {}).get(instance.get("installation"), {}).get("plugins") or {}
    if isinstance(plugins, dict):
        inst_exts.update(plugins.keys())
    nodes.append({
        "id": name,
        "label": name,
        "host": instance["host_config"]["address"],
        "port": instance["port"],
        "data_dir": instance["data_dir"],
        "role": "instance",
        "extensions": sorted(inst_exts),
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

cluster_exts = set()
if kind == "mmr":
    cluster_exts.update((config.mmr_clusters.get(cluster_name) or {}).get("extensions") or [])
elif kind == "citus":
    cluster_exts.update((config.citus_clusters.get(cluster_name) or {}).get("extensions") or [])
if cluster_exts:
    for node in nodes:
        node["extensions"] = sorted(set(node["extensions"]) | cluster_exts)

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

stream_db = ((config.mmr_clusters.get(cluster_name) or {}).get("database")
             or (config.citus_clusters.get(cluster_name) or {}).get("database")
             or "postgres")
stream_installed = {}
for stream_name in streaming_names:
    stream = config.streaming_clusters[stream_name]
    members = [stream["primary"]] + [item["instance"] for item in stream.get("standbys") or []]
    try:
        live_primary = runtime._primary(stream_name)
        if live_primary in members:
            members.remove(live_primary)
            members.insert(0, live_primary)
    except Exception:
        pass
    stream_installed[stream_name] = installed_extensions(members[:2], stream_db)
for node in nodes:
    node["installed_extensions"] = stream_installed.get(node.get("group"), [])

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
runtime = Runtime(config)
status = runtime.status_display(target)

# 为各运行中实例探测 postmaster (pgmaster) 根守护进程 PID
import re
for name, info in status.items():
    if not isinstance(info, dict):
        continue
    pid = None
    if info.get("running"):
        # 1. 优先从 pg_ctl status 回显中直接提取 PID: "pg_ctl: server is running (PID: 14871)"
        msg = info.get("message") or ""
        m = re.search(r"\(PID:\s*(\d+)\)", msg)
        if m:
            try:
                pid = int(m.group(1))
            except (ValueError, TypeError):
                pid = None
        # 2. 回退到读取 ${data_dir}/postmaster.pid 首行
        if not pid:
            try:
                inst = config.instance(name)
                data_dir = inst.get("data_dir")
                address = (inst.get("host_config") or {}).get("address")
                if data_dir:
                    host = "local" if runtime.executor.is_local(address) else address
                    proc = runtime.executor.run(
                        ["head", "-n", "1", f"{data_dir}/postmaster.pid"],
                        host=host, check=False)
                    if proc.returncode == 0 and proc.stdout.strip().isdigit():
                        pid = int(proc.stdout.strip())
            except Exception:
                pass
    info["pid"] = pid

print(json.dumps(status, ensure_ascii=False))
"""


def _deployment(environment: dict) -> tuple[Path, str]:
    path = Path(environment.get("deployment_config") or "")
    target = environment.get("deployment_target")
    if not path.is_file() or not target:
        raise ValueError("请先登记有效的 pgcluster 配置和目标")
    return path, target


def topology(settings: Settings, environment: dict) -> dict:
    path, target = _deployment(environment)
    script = "import sys\nsys.path.insert(0,"+repr(str(ROOT/"backend"))+")\nfrom platform_app.resources import local_addresses\nfrom pgclusterlib.executor import LOCAL_HOSTS\nLOCAL_HOSTS.update(local_addresses())\n"+TOPOLOGY_SCRIPT
    result = subprocess.run(
        [sys.executable, "-c", script, str(path), target],
        cwd=settings.pgcluster_root,
        capture_output=True,
        text=True,
        timeout=15,
    )
    if result.returncode:
        raise ValueError(result.stderr.strip() or "无法读取部署拓扑")
    return json.loads(result.stdout)


def status(settings: Settings, environment: dict) -> dict:
    path, target = _deployment(environment)
    script = "import sys\nsys.path.insert(0,"+repr(str(ROOT/"backend"))+")\nfrom platform_app.resources import local_addresses\nfrom pgclusterlib.executor import LOCAL_HOSTS\nLOCAL_HOSTS.update(local_addresses())\n"+STATUS_SCRIPT
    try:
        result = subprocess.run(
            [sys.executable, "-c", script, str(path), target],
            cwd=settings.pgcluster_root, capture_output=True, text=True, timeout=30,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("读取节点状态超时") from exc
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "读取节点状态失败")
    return json.loads(result.stdout)
