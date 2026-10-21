"""PostgreSQL 17 deployment templates: single node and primary/standby pairs.

Extensions default to pg_deltax + TimescaleDB and remain editable through
``spec.extensions``; binaries must already be installed into each PGHOME.
"""

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
    host_entries = list(spec.hosts)
    if host_entries:
        hosts_section = {}
        for host in host_entries:
            entry = {"address": host.address}
            ssh = host.ssh_options()
            if ssh:
                entry["ssh"] = ssh
            hosts_section[host.name] = entry
        default_host = next(
            host.name for host in host_entries if host.address == spec.host
        )
        homes = {host.name: host.home or spec.home for host in host_entries}
    else:
        hosts_section = {"deploy_host": {"address": spec.host}}
        default_host = "deploy_host"
        homes = {default_host: spec.home}
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
    extensions = [
        (item.name, bool(item.preload)) for item in spec.extensions
    ] or list(DEFAULT_EXTENSIONS)
    ext_names = list(dict.fromkeys(name for name, _ in extensions))
    plugins = {}
    for name, preload in extensions:
        entry = {"required": True, "extension": name}
        if preload:
            entry["preload_library"] = name
        plugins[name] = entry
    install_by_home = {}
    installations = {}
    install_for_host = {}
    for host_name in hosts_section:
        home = homes[host_name]
        if home not in install_by_home:
            install_name = (
                "deploy_postgres"
                if not install_by_home
                else "deploy_postgres_" + host_name
            )
            install_by_home[home] = install_name
            installations[install_name] = {
                "provider": "postgres",
                "home": home,
                "plugins": plugins,
            }
        install_for_host[host_name] = install_by_home[home]
    parameters = {
        "wal_level": "logical",
        "hot_standby": "on",
        "listen_addresses": "*",
        "logging_collector": "on",
        "log_destination": "stderr,csvlog",
        "log_directory": "log",
        **spec.parameters,
    }
    existing_preloads = parameters.get("shared_preload_libraries")
    if isinstance(existing_preloads, str):
        existing_preloads = [
            item.strip() for item in existing_preloads.split(",") if item.strip()
        ]
    elif not isinstance(existing_preloads, list):
        existing_preloads = []
    extra_preloads = [
        name for name, preload in extensions
        if preload and name not in existing_preloads
    ]
    if extra_preloads:
        parameters["shared_preload_libraries"] = [
            *existing_preloads, *extra_preloads
        ]
    config = {
        "hosts": hosts_section,
        "postgresql_installations": installations,
        "postgresql_config": {
            "parameters": parameters,
            "replication_capacity": {
                "wal_senders": "auto",
                "replication_slots": "auto",
            },
            "hba": [
                {
                    "type": "local",
                    "database": "all",
                    "user": "all",
                    "auth_method": "trust",
                },
                {
                    "type": "host",
                    "database": "all",
                    "user": "all",
                    "address": "0.0.0.0/0",
                    "auth_method": "trust",
                },
                {
                    "type": "host",
                    "database": "replication",
                    "user": "all",
                    "address": "0.0.0.0/0",
                    "auth_method": "trust",
                },
            ],
        },
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
