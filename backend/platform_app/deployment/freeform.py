"""Free-topology compiler: user-modeled single streaming cluster -> pgcluster config.

Product-neutral: fixed regression topologies stay in product templates; this
covers the generic primary + standbys case the workbench models freely.
"""

import re
from pathlib import Path

from .builder import (
    DEFAULT_PG_PARAMETERS,
    build_hosts_section,
    build_installations,
    build_postgresql_config,
    compile_extensions,
    merge_preload_libraries,
)

_IDENTIFIER = re.compile(r"[a-zA-Z_]\w*")
ROOT = Path(__file__).resolve().parents[3]


def compile_freeform(spec):
    if not spec.nodes:
        raise ValueError("自由拓扑至少需要一个节点")
    primaries = [node for node in spec.nodes if node.role == "primary"]
    if len(primaries) != 1:
        raise ValueError("自由拓扑需要且只能有一个主节点")
    names = [node.name for node in spec.nodes]
    if len(set(names)) != len(names):
        raise ValueError("节点名称不能重复")
    if not _IDENTIFIER.fullmatch(spec.cluster_name):
        raise ValueError("集群名称无效")
    for name in names:
        if not _IDENTIFIER.fullmatch(name):
            raise ValueError(f"节点名称 {name} 无效")
    if not spec.home:
        raise ValueError("请填写数据库安装目录")
    if Path(spec.home).is_relative_to(ROOT / "data"):
        raise ValueError("数据库安装目录不能位于平台控制面目录")

    hosts_section, default_host, homes = build_hosts_section(spec)
    ext_names, plugins, preloads = compile_extensions(spec.extensions)
    installations, install_for_host = build_installations(
        hosts_section,
        homes,
        provider="fbase" if spec.license_file else "postgres",
        plugins=plugins or None,
        license_file=spec.license_file or None,
    )

    instances = {}
    for node in spec.nodes:
        host_name = node.host or default_host
        if host_name not in hosts_section:
            raise ValueError(f"节点 {node.name} 的主机 {host_name} 未在主机资源中声明")
        instances[node.name] = {
            "host": host_name,
            "installation": install_for_host[host_name],
            "port": node.port,
            "data_dir": node.data_dir,
        }

    parameters = merge_preload_libraries(
        {**DEFAULT_PG_PARAMETERS, **spec.parameters}, preloads
    )
    config = {
        "hosts": hosts_section,
        "postgresql_installations": installations,
        "postgresql_config": build_postgresql_config(parameters),
        "instances": instances,
        "streaming_clusters": {
            spec.cluster_name: {
                "primary": primaries[0].name,
                "standbys": [
                    {"instance": node.name}
                    for node in spec.nodes
                    if node.role == "standby"
                ],
                "replication": {"mode": "async"},
                **({"extensions": ext_names} if ext_names else {}),
            }
        },
    }
    return config, "streaming." + spec.cluster_name, {}
