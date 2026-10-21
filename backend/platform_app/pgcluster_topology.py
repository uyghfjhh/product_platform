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
from pgclusterlib.sql import quote_literal

config = load(sys.argv[1])
target = sys.argv[2]
config.validate(target)
runtime = Runtime(config)
names = runtime.target_instances(target)

# 节点版本、实际角色与已安装插件来自各物理实例，禁止借用其他节点的
# 版本或角色；不可达时保留配置声明与受管角色记录，版本保持未知。
def installed_extensions(member_names, databases):
    out = {}
    for database in databases:
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
                     "-c", "SELECT json_build_object('server_version',current_setting('server_version'),'database_version',version(),'in_recovery',pg_is_in_recovery(),'extension_versions',(SELECT json_object_agg(extname,extversion) FROM pg_extension))"],
                    host=host, check=False)
                if proc.returncode == 0 and proc.stdout.strip():
                    row = json.loads(proc.stdout.strip())
                    for key in ("server_version", "database_version", "in_recovery"):
                        out.setdefault(key, row.get(key))
                    versions = out.setdefault("extension_versions", {})
                    versions.update(row.get("extension_versions") or {})
                    break
            except Exception:
                continue
    return out

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

for stream_name in streaming_names:
    stream = config.streaming_clusters[stream_name]
    primary = runtime._primary(stream_name)
    members = [stream['primary']] + [item['instance'] for item in stream.get('standbys') or []]
    for node in nodes:
        if node['id'] in members:
            node['role'] = 'primary' if node['id'] == primary else 'standby'
            node['group'] = stream_name
    for secondary in members:
        if secondary != primary:
            edges.append({'id':'stream:'+primary+':'+secondary,'source':primary,'target':secondary,'kind':'streaming'})

# Per-group merge: a node inherits the extensions declared on its own
# streaming cluster (plus the top-level cluster extensions for mmr/citus).
for node in nodes:
    merged = set(cluster_exts)
    stream = config.streaming_clusters.get(node.get("group")) or {}
    merged.update(stream.get("extensions") or [])
    if merged:
        node["extensions"] = sorted(set(node["extensions"]) | merged)

if kind == "logical":
    # 同一对 streaming 集群间可能挂多条逻辑链路（如多库对比环境）——全部画出来
    pair = (streaming_names[0], streaming_names[1])
    link_names = [lname for lname, item in config.logical_replications.items()
                  if (item["pub"]["streaming_cluster"], item["sub"]["streaming_cluster"]) == pair]
    for lname in link_names:
        lnk = config.logical_replications[lname]
        left = config.streaming_clusters[lnk["pub"]["streaming_cluster"]]["primary"]
        right = config.streaming_clusters[lnk["sub"]["streaming_cluster"]]["primary"]
        edges.append({
            "id": "logical:" + lname + ":" + left + ":" + right,
            "source": left,
            "target": right,
            "kind": "logical",
            "link": {
                "name": lname,
                "pub_cluster": lnk["pub"]["streaming_cluster"],
                "sub_cluster": lnk["sub"]["streaming_cluster"],
                "database": lnk["pub"].get("database", "postgres"),
                "sub_database": lnk["sub"].get("database", "postgres"),
                "publication": "%s_pub" % lname,
                "subscription": "%s_sub" % lname,
                "slot": (lnk["sub"].get("slot") or {}).get("name") or "%s_slot" % lname,
                "tables": lnk["pub"].get("tables") or "all",
                "via_partition_root": bool(lnk["pub"].get("via_partition_root")),
                "publish": lnk["pub"].get("publish"),
                "live": {},
            },
        })

        # 发布端 pg_stat_replication 探测订阅 apply worker 的活连接与回放延迟
        instance = config.instance(left)
        host = "local" if runtime.executor.is_local(instance["host_config"]["address"]) \
            else instance["host_config"]["address"]
        try:
            proc = runtime.executor.run(
                ["env", "PGCONNECT_TIMEOUT=3",
                 runtime._bin(left, "psql"), "-X", "-v", "ON_ERROR_STOP=1",
                 "-h", instance["host_config"]["address"], "-p", str(instance["port"]),
                 "-U", "postgres", "-d", lnk["pub"].get("database", "postgres"), "-At",
                 "-c", "SELECT json_build_object('connected',count(*)>0,'state',coalesce(max(state),'disconnected'),'senders',count(*),'replay_lag_bytes',coalesce(max(pg_wal_lsn_diff(pg_current_wal_lsn(),replay_lsn)),0)::bigint) FROM pg_stat_replication WHERE application_name=%s"
                       % quote_literal("%s_sub" % lname)],
                host=host, check=False)
            if proc.returncode == 0 and proc.stdout.strip():
                edges[-1]["link"]["live"] = json.loads(proc.stdout.strip())
        except Exception:
            pass
if kind == "mmr":
    members = [runtime._primary(name) for name in streaming_names]
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
# Query each physical instance: standby binaries can have different versions.
# Bound parallel probes so stopped nodes do not serially exhaust the API timeout.
# 扩展按 streaming_clusters.databases 逐库探测并合并版本——数据库级扩展
# （如逻辑复制环境里的 pg_deltax/timescaledb）不在默认库里。
from concurrent.futures import ThreadPoolExecutor
def node_databases(node):
    stream = config.streaming_clusters.get(node.get("group")) or {}
    dbs = [stream_db] + sorted((stream.get("databases") or {}).keys())
    return list(dict.fromkeys(dbs))
with ThreadPoolExecutor(max_workers=8) as pool:
    live_nodes = list(pool.map(lambda node: installed_extensions([node['id']], node_databases(node)), nodes))
for node, live in zip(nodes, live_nodes):
    node['extension_versions'] = live.get('extension_versions') or {}
    node['installed_extensions'] = sorted(node['extension_versions'])
    node['server_version'] = live.get('server_version')
    node['database_version'] = live.get('database_version')
    node['declared_role'] = node['role']
    if live.get('in_recovery') is not None:
        node['role'] = 'standby' if live['in_recovery'] else 'primary'
    elif node.get('group'):
        node['role'] = 'primary' if runtime._primary(node['group']) == node['id'] else 'standby'
    node['original_primary'] = config.streaming_clusters.get(node.get('group'), {}).get('primary')
    node['previous_primary'] = runtime._state().get('failovers', {}).get(node.get('group'), {}).get('old_primary')
    node['rejoin_required'] = bool(node['previous_primary'] and not runtime._state().get('failovers', {}).get(node.get('group'), {}).get('rejoined'))


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
