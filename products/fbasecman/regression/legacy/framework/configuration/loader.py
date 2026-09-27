"""Layered YAML configuration and standard artifact paths for fbasecman."""

import platform_regress.configuration.loader as _platform_loader
from platform_regress.configuration.loader import (  # noqa: F401
    RegressionConfig, _deep_merge, _parse_legacy_config, _strip_legacy_value,
)
from framework.configuration.validation import (  # noqa: F401
    ConfigurationError, isolation_errors, validate_config,
)


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
