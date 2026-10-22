"""Shared pgcluster configuration builders for product templates and freeform specs."""

from copy import deepcopy

DEFAULT_PG_PARAMETERS = {
    "wal_level": "logical",
    "hot_standby": "on",
    "listen_addresses": "*",
    "logging_collector": "on",
    "log_destination": "stderr,csvlog",
    "log_directory": "log",
}

DEFAULT_HBA = (
    {"type": "local", "database": "all", "user": "all", "auth_method": "trust"},
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
)


def build_hosts_section(spec):
    """Build pgcluster ``hosts`` mapping, primary host key, and per-host PGHOME map."""
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
    return hosts_section, default_host, homes


def build_installations(
    hosts_section,
    homes,
    *,
    provider="postgres",
    plugins=None,
    license_file=None,
    include_license=False,
    base_installation=None,
    name_prefix="deploy_postgres",
):
    """Deduplicate installations by PGHOME and map each host to its installation key."""
    install_by_home = {}
    installations = {}
    install_for_host = {}
    for host_name in hosts_section:
        home = homes[host_name]
        if home not in install_by_home:
            install_name = (
                name_prefix
                if not install_by_home
                else f"{name_prefix}_{host_name}"
            )
            install_by_home[home] = install_name
            if base_installation is not None:
                entry = {**deepcopy(base_installation), "home": home}
            else:
                entry = {"provider": provider, "home": home}
                if plugins is not None:
                    entry["plugins"] = deepcopy(plugins)
                if include_license or license_file:
                    entry["license"] = {
                        "source_file": license_file,
                        "data_file": "license.dat",
                    }
            installations[install_name] = entry
        install_for_host[host_name] = install_by_home[home]
    return installations, install_for_host


def compile_extensions(extensions, defaults=()):
    """Deduplicate extension declarations into ``(ext_names, plugins_map, preload_list)``."""
    items = [(item.name, bool(item.preload)) for item in extensions] or list(defaults)
    names, plugins, preloads = [], {}, []
    for name, preload in items:
        if name in plugins:
            continue
        plugin = {"required": True, "extension": name}
        if preload:
            plugin["preload_library"] = name
            preloads.append(name)
        plugins[name] = plugin
        names.append(name)
    return names, plugins, preloads


def merge_preload_libraries(parameters, preloads):
    """Append ``preloads`` into ``parameters['shared_preload_libraries']`` without duplicates."""
    existing = parameters.get("shared_preload_libraries")
    if isinstance(existing, str):
        existing = [item.strip() for item in existing.split(",") if item.strip()]
    elif not isinstance(existing, list):
        existing = []
    extra = [name for name in preloads if name not in existing]
    if extra:
        parameters["shared_preload_libraries"] = [*existing, *extra]
    return parameters


def build_postgresql_config(parameters):
    """Wrap ``parameters`` with standard replication capacity and permissive HBA rules."""
    return {
        "parameters": parameters,
        "replication_capacity": {
            "wal_senders": "auto",
            "replication_slots": "auto",
        },
        "hba": [dict(rule) for rule in DEFAULT_HBA],
    }
