"""fbasecman regression profile machinery (product-private, not platform).

Consolidates the former ``framework.configuration`` / ``framework.environment``
glue: legacy YAML mapping, typed config validation, stable/regression
isolation checks, the deployment preflight hook, and suite-registry defaults.
Generic primitives live in ``platform_regress``; this module only carries
fbasecman-specific semantics.
"""

from pathlib import Path
from typing import Any, Dict

import platform_regress.configuration.loader as _platform_loader
import platform_regress.environment.sanitizer as _sanitizer
import platform_regress.suites.registry as _suite_registry
from platform_regress.configuration.loader import (  # noqa: F401
    RegressionConfig, _deep_merge, _parse_legacy_config, _strip_legacy_value,
)
from platform_regress.configuration.validation import (  # noqa: F401
    ConfigurationError,
    port_value as _port,
    reject_unknown as _reject_unknown,
    require_mapping as _require_mapping,
    require_text as _require_text,
)
from platform_regress.environment.registry import (  # noqa: F401
    create_environment_provider, register_environment_provider,
)


PORT_NAMES = (
    "mmr1", "mmr1_standby1", "mmr1_standby2", "mmr1_standby3",
    "mmr2", "mmr2_standby1", "mmr2_standby2", "mmr2_standby3",
)
OPTIONAL_PORT_NAMES = (
    "rep_primary", "rep_standby1", "rep_standby2",
)
STANDBY_LIST_NAMES = ("mmr1_standbys", "mmr2_standbys", "rep_standbys")

SECTION_FIELDS = {
    "fbasecman": {"fbasecman_bin", "write_port", "read_port", "log_level", "license_dir"},
    "database": {
        "mmr_host", "rep_host", "mmr_pg_user", "rep_pg_user",
        "mmr_postgres_dir", "rep_postgres_dir", "mmr_data_root",
        "rep_data_root", "ports", "enable_citus",
    },
    "local": {"postgres_dir", "jdbc_lib_dir", "jdbc_versions"},
    "framework": {
        "output_dir", "environment_output_dir", "default_timeout",
        "keep_workdir", "fail_fast", "legacy_config",
    },
    "stable": {
        "pgbench_duration", "jdbc_duration", "interval_seconds",
        "server_lifetime", "global_prepared_statements_limit",
        "backend_prepared_statements_limit", "enabled_workloads", "ha_commands",
        "reload_status_toggle", "jdbc",
    },
}

STABLE_JDBC_FIELDS = {
    "enabled", "long_conn_clients", "short_conn_clients", "shared_sql_count",
    "private_sql_count", "short_conn_batch", "short_conn_idle_ms",
    "rw_switch_enabled", "rw_switch_interval_ops", "heartbeat_enabled",
    "heartbeat_interval_ops", "guc_enabled", "guc_interval_ops",
    "guc_distinct_count",
}

STABLE_RELOAD_STATUS_TOGGLE_FIELDS = {
    "enabled", "datasource", "interval_seconds",
}
STABLE_HA_COMMAND_FIELDS = {"enabled"}


def validate_config(config, profile):
    """Validate the complete merged configuration used by a public command."""
    if not isinstance(config, dict):
        raise ConfigurationError("configuration root must be a mapping")
    allowed_sections = {"fbasecman", "database", "local", "framework"}
    if profile == "stable":
        allowed_sections.add("stable")
    _reject_unknown(config, allowed_sections, "configuration root")

    fbasecman = _require_mapping(config, "fbasecman")
    database = _require_mapping(config, "database")
    local = _require_mapping(config, "local")
    framework = _require_mapping(config, "framework")
    for name, section in (("fbasecman", fbasecman), ("database", database),
                          ("local", local), ("framework", framework)):
        _reject_unknown(section, SECTION_FIELDS[name], name)

    _require_text(fbasecman, ("fbasecman_bin", "license_dir"), "fbasecman")
    _require_text(database, (
        "mmr_host", "mmr_pg_user", "mmr_postgres_dir",
    ), "database")
    _require_text(local, ("postgres_dir", "jdbc_lib_dir"), "local")
    _require_text(framework, ("output_dir",), "framework")
    if "enable_citus" in database and not isinstance(database["enable_citus"], bool):
        raise ConfigurationError("database.enable_citus must be a boolean")

    ports = _require_mapping(database, "ports")
    allowed_ports = PORT_NAMES + OPTIONAL_PORT_NAMES + STANDBY_LIST_NAMES
    _reject_unknown(ports, allowed_ports, "database.ports")
    missing = [name for name in PORT_NAMES if name not in ports]
    if missing:
        raise ConfigurationError("missing database port(s): %s" % ", ".join(missing))
    primary_names = ["mmr1", "mmr2"]
    if "rep_primary" in ports:
        primary_names.append("rep_primary")
    values = [_port(ports[name], "database.ports.%s" % name) for name in primary_names]
    list_clusters = {name[:-9] for name in STANDBY_LIST_NAMES if name in ports}
    all_check_ports = PORT_NAMES + tuple(p for p in OPTIONAL_PORT_NAMES if p in ports)
    values.extend(
        _port(ports[name], "database.ports.%s" % name)
        for name in all_check_ports if "_standby" in name and name.split("_standby", 1)[0] not in list_clusters
    )
    for name in STANDBY_LIST_NAMES:
        configured = ports.get(name)
        if configured is None:
            continue
        if not isinstance(configured, list) or not configured:
            raise ConfigurationError("database.ports.%s must be a non-empty list" % name)
        values.extend(_port(value, "database.ports.%s" % name) for value in configured)
    for name in ("read_port", "write_port"):
        values.append(_port(fbasecman.get(name), "fbasecman.%s" % name))
    values.append(_port(fbasecman["read_port"] + 2, "fbasecman prometheus port"))
    if len(values) != len(set(values)):
        raise ConfigurationError("database and fbasecman ports must be unique within %s" % profile)

    timeout = framework.get("default_timeout", 60)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0:
        raise ConfigurationError("framework.default_timeout must be greater than zero")

    if profile == "stable":
        stable = _require_mapping(config, "stable")
        _reject_unknown(stable, SECTION_FIELDS["stable"], "stable")
        enabled_workloads = stable.get("enabled_workloads")
        if enabled_workloads is not None and (
                not isinstance(enabled_workloads, list) or
                not all(isinstance(item, str) and item for item in enabled_workloads)):
            raise ConfigurationError("stable.enabled_workloads must be a list of non-empty strings")
        jdbc = stable.get("jdbc", {})
        if not isinstance(jdbc, dict):
            raise ConfigurationError("stable.jdbc must be a mapping")
        _reject_unknown(jdbc, STABLE_JDBC_FIELDS, "stable.jdbc")
        if "enabled" in jdbc and not isinstance(jdbc["enabled"], bool):
            raise ConfigurationError("stable.jdbc.enabled must be a boolean")
        reload_toggle = stable.get("reload_status_toggle", {})
        if not isinstance(reload_toggle, dict):
            raise ConfigurationError("stable.reload_status_toggle must be a mapping")
        _reject_unknown(
            reload_toggle, STABLE_RELOAD_STATUS_TOGGLE_FIELDS,
            "stable.reload_status_toggle",
        )
        if "enabled" in reload_toggle and not isinstance(reload_toggle["enabled"], bool):
            raise ConfigurationError(
                "stable.reload_status_toggle.enabled must be a boolean"
            )
        if "datasource" in reload_toggle and not isinstance(reload_toggle["datasource"], str):
            raise ConfigurationError("stable.reload_status_toggle.datasource must be a string")
        if "interval_seconds" in reload_toggle:
            interval = reload_toggle["interval_seconds"]
            if isinstance(interval, bool) or not isinstance(interval, (int, float)) or interval < 0:
                raise ConfigurationError(
                    "stable.reload_status_toggle.interval_seconds must be a non-negative number"
                )
        ha_commands = stable.get("ha_commands", {})
        if not isinstance(ha_commands, dict):
            raise ConfigurationError("stable.ha_commands must be a mapping")
        _reject_unknown(ha_commands, STABLE_HA_COMMAND_FIELDS, "stable.ha_commands")
        if "enabled" in ha_commands and not isinstance(ha_commands["enabled"], bool):
            raise ConfigurationError("stable.ha_commands.enabled must be a boolean")
    return config


def _resolved_root(config, name):
    database = config["database"]
    fallback = database.get("mmr_postgres_dir" if name == "mmr_data_root" else "rep_postgres_dir")
    val = database.get(name, fallback)
    if not val:
        return None
    return str(Path(val).resolve())


def _environment_output(root, config):
    framework = config["framework"]
    value = framework.get("environment_output_dir")
    if value:
        return str((Path(root) / value).resolve())
    return str((Path(root) / framework["output_dir"] / "env").resolve())


def isolation_errors(root, regress, stable):
    """Return actionable stable/regression resource collision errors."""
    errors = []
    def configured_ports(config):
        values = []
        for value in config["database"]["ports"].values():
            values.extend(value if isinstance(value, list) else [value])
        return set(values)
    regress_ports = configured_ports(regress)
    regress_ports.update((regress["fbasecman"]["read_port"], regress["fbasecman"]["write_port"],
                          regress["fbasecman"]["read_port"] + 2))
    stable_ports = configured_ports(stable)
    stable_ports.update((stable["fbasecman"]["read_port"], stable["fbasecman"]["write_port"],
                         stable["fbasecman"]["read_port"] + 2))
    overlap = sorted(regress_ports & stable_ports)
    if overlap:
        errors.append("stable/regression ports overlap: %s" % ", ".join(str(item) for item in overlap))

    regress_roots = {_resolved_root(regress, name) for name in ("mmr_data_root", "rep_data_root")}
    regress_roots.discard(None)
    stable_roots = {_resolved_root(stable, name) for name in ("mmr_data_root", "rep_data_root")}
    stable_roots.discard(None)
    for regress_root in sorted(regress_roots):
        for stable_root in sorted(stable_roots):
            regress_path = Path(regress_root)
            stable_path = Path(stable_root)
            if regress_path == stable_path or regress_path in stable_path.parents or stable_path in regress_path.parents:
                errors.append("stable/regression PGDATA roots overlap: %s and %s" % (
                    regress_root, stable_root,
                ))
    regress_env = _environment_output(root, regress)
    stable_env = _environment_output(root, stable)
    regress_env_path = Path(regress_env)
    stable_env_path = Path(stable_env)
    if (regress_env_path == stable_env_path or regress_env_path in stable_env_path.parents or
            stable_env_path in regress_env_path.parents):
        errors.append("stable/regression environment output overlaps: %s and %s" % (
            regress_env, stable_env,
        ))
    return errors


def _legacy_to_regress_config(values):
    if not values:
        return {}

    def int_value(name):
        return int(values[name]) if name in values and str(values[name]).isdigit() else None

    mapped = {"fbasecman": {}, "database": {"ports": {}}, "local": {}}
    key_map = {
        ("fbasecman", "fbasecman_bin"): "FBASECMAN_BIN",
        ("fbasecman", "write_port"): "FBASECMAN_PORT",
        ("fbasecman", "read_port"): "OTHER_PORT",
        ("fbasecman", "log_level"): "TEST_LOG_LEVEL",
        ("fbasecman", "license_dir"): "LICENSE_DIR",
        ("database", "mmr_host"): "MMR_HOST",
        ("database", "rep_host"): "REP_HOST",
        ("database", "mmr_pg_user"): "MMR_PG_USER",
        ("database", "rep_pg_user"): "REP_PG_USER",
        ("database", "mmr_postgres_dir"): "MMR_POSTGRES_DIR",
        ("database", "rep_postgres_dir"): "REP_POSTGRES_DIR",
        ("local", "postgres_dir"): "LOCAL_POSTGRES_DIR",
        ("local", "jdbc_lib_dir"): "LIB_JDBC",
    }
    for (section, key), legacy_key in key_map.items():
        if legacy_key in values:
            mapped[section][key] = values[legacy_key]

    port_map = {
        "mmr1": "MMR1_PORT", "mmr2": "MMR2_PORT",
        "mmr1_standby1": "MMR1_S1_PORT", "mmr1_standby2": "MMR1_S2_PORT",
        "mmr1_standby3": "MMR1_S3_PORT", "mmr2_standby1": "MMR2_S1_PORT",
        "mmr2_standby2": "MMR2_S2_PORT", "mmr2_standby3": "MMR2_S3_PORT",
        "rep_primary": "REP_PORT", "rep_standby1": "REP_S1_PORT",
        "rep_standby2": "REP_S2_PORT",
    }
    for key, legacy_key in port_map.items():
        value = int_value(legacy_key)
        if value is not None:
            mapped["database"]["ports"][key] = value
    if "JDBC_VERSIONS" in values:
        mapped["local"]["jdbc_versions"] = values["JDBC_VERSIONS"]
    return mapped


def load_config(root_dir, config_name, local_config_name=None, extra_configs=None,
                validate=False, profile=None):
    return _platform_loader.load_config(
        root_dir, config_name, local_config_name=local_config_name,
        extra_configs=extra_configs, validate=validate, profile=profile,
        validator=validate_config, legacy_mapper=_legacy_to_regress_config,
    )


def load_regression_config(root_dir, extra_configs=None, validate=True):
    return load_config(
        root_dir, "regress.yaml", extra_configs=extra_configs,
        validate=validate, profile="regression",
    )


def validate_profile_isolation(environment):
    """Ensure stable and regression cannot address the same persistent resources."""
    return _platform_loader.validate_profile_isolation(
        environment, loader=load_config, isolation_check=isolation_errors,
    )


def _health_errors(health, config):
    """Compare the baseline indicators used by the deployment health gate."""
    ports = config["database"]["ports"]
    expected_streaming = len(ports.get("mmr1_standbys", (
        ports.get("mmr1_standby1"),
        ports.get("mmr1_standby2"),
        ports.get("mmr1_standby3"),
    )))
    expected = {
        "mmr_non_active": {"0"},
        "testdb_node1": {"ACTIVE"},
        "testdb_node2": {"JOIN_START", "ACTIVE"},
        "mmr_streaming": {str(expected_streaming)},
    }
    return [
        "%s=%s (expected %s)" % (key, health.get(key, "<missing>"), "/".join(sorted(values)))
        for key, values in expected.items()
        if str(health.get(key, "<missing>")) not in values
    ]


def preflight_health_check(root_dir: Path, auto_heal: bool = True) -> Dict[str, Any]:
    """Check the test environment and report failure without hiding its cause."""
    return _sanitizer.preflight_health_check(
        root_dir, "fbasecman", load_regression_config,
        expected_errors=_health_errors, auto_heal=auto_heal,
        provider_factory=create_environment_provider,
    )


def _preflight(root_dir):
    return preflight_health_check(root_dir, auto_heal=True)


_suite_registry.set_default_preflight_check(_preflight)
_suite_registry.set_default_quiet_env_var("FBASECMAN_QUIET_ENV")

import platform_regress.runtime as _runtime  # noqa: E402

_runtime.set_legacy_config_loader(load_regression_config)
