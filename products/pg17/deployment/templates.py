"""PostgreSQL 17 deployment templates: single node and primary/standby pairs.

Extensions default to pg_deltax + TimescaleDB and remain editable through
``spec.extensions``; binaries must already be installed into each PGHOME.
"""

from platform_app.deployment.builder import (
    DEFAULT_PG_PARAMETERS,
    build_hosts_section,
    build_installations,
    build_postgresql_config,
    compile_extensions,
    merge_preload_libraries,
)

TOPOLOGIES = {
    "single": [("primary", "primary")],
    "ha": [("primary", "primary"), ("standby1", "standby")],
}

DEFAULT_EXTENSIONS = (("pg_deltax", True), ("timescaledb", True))


def templates(settings):
    defaults = [
        {"name": name, "preload": preload}
        for name, preload in DEFAULT_EXTENSIONS
    ]
    return [
        {
            "id": "ha",
            "title": "一主一备（pg_deltax + TimescaleDB）",
            "nodes": 2,
            "base_port": 25432,
            "target": "streaming.cluster",
            "extensions_editable": True,
            "default_extensions": defaults,
        },
        {
            "id": "single",
            "title": "单机（pg_deltax + TimescaleDB）",
            "nodes": 1,
            "base_port": 25432,
            "target": "streaming.cluster",
            "extensions_editable": True,
            "default_extensions": defaults,
        },
    ]


def compile_template(settings, spec, environment_id):
    topology = TOPOLOGIES.get(spec.template_id)
    if topology is None:
        raise ValueError("未知部署模板: %s" % spec.template_id)
    cluster_name = spec.cluster_name or "cluster"
    hosts_section, default_host, homes = build_hosts_section(spec)
    ordered_hosts = [default_host] + [
        name for name in hosts_section if name != default_host
    ]
    roles = {}
    nodes = {}
    for index, (suffix, role) in enumerate(topology):
        name = "%s_%s" % (cluster_name, suffix)
        roles[name] = role
        nodes[name] = {
            "host": ordered_hosts[index % len(ordered_hosts)],
            "port": spec.base_port + index,
            "data_dir": "%s/%s" % (spec.data_root, name),
        }
    if spec.nodes:
        if {node.name for node in spec.nodes} != set(nodes):
            raise ValueError("实例列表与模板布局不一致，请重新生成布局")
        for node in spec.nodes:
            if node.host:
                if node.host not in hosts_section:
                    raise ValueError("节点主机未在主机资源中声明: %s" % node.host)
                nodes[node.name]["host"] = node.host
            nodes[node.name]["port"] = node.port
            nodes[node.name]["data_dir"] = node.data_dir
    ext_names, plugins, preloads = compile_extensions(
        spec.extensions, DEFAULT_EXTENSIONS
    )
    installations, install_for_host = build_installations(
        hosts_section, homes, provider="postgres", plugins=plugins
    )
    parameters = merge_preload_libraries(
        {**DEFAULT_PG_PARAMETERS, **spec.parameters}, preloads
    )
    config = {
        "hosts": hosts_section,
        "postgresql_installations": installations,
        "postgresql_config": build_postgresql_config(parameters),
        "instances": {
            name: {
                "host": node["host"],
                "installation": install_for_host[node["host"]],
                "port": node["port"],
                "data_dir": node["data_dir"],
            }
            for name, node in nodes.items()
        },
        "streaming_clusters": {
            cluster_name: {
                "primary": next(
                    name for name, role in roles.items() if role == "primary"
                ),
                "standbys": [
                    {"instance": name}
                    for name, role in roles.items()
                    if role == "standby"
                ],
                "replication": {"mode": "async"},
                "extensions": ext_names,
            }
        },
    }
    return config, "streaming." + cluster_name, {}
