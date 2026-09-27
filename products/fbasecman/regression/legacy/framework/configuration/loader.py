"""Layered YAML configuration and standard artifact paths."""

import re
from pathlib import Path

import yaml

from .validation import ConfigurationError, isolation_errors, validate_config


def _deep_merge(base, override):
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _strip_legacy_value(value):
    value = value.strip()
    if "#" in value:
        value = value.split("#", 1)[0].strip()
    if value.startswith('"') and value.endswith('"'):
        return value[1:-1]
    if value.startswith("'") and value.endswith("'"):
        return value[1:-1]
    return value


def _parse_legacy_config(path):
    values = {}
    if not path.exists():
        return values
    line_re = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$")
    array_re = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=\((.*)\)$")
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        array_match = array_re.match(line)
        if array_match:
            key, raw_values = array_match.groups()
            values[key] = [
                _strip_legacy_value(item) for item in raw_values.split() if item.strip()
            ]
            continue
        match = line_re.match(line)
        if match:
            key, raw_value = match.groups()
            values[key] = _strip_legacy_value(raw_value)
    return values


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


class RegressionConfig(object):
    def __init__(self, root_dir, config, profile="regression", sources=None):
        self.root_dir = Path(root_dir)
        self.config = config
        self.profile = profile
        self.sources = tuple(Path(path).resolve() for path in (sources or ()))

    @property
    def output_dir(self):
        return self.root_dir / self.config["framework"]["output_dir"]

    @property
    def env_output_dir(self):
        configured = self.config.get("framework", {}).get("environment_output_dir")
        if configured:
            return self.root_dir / configured
        return self.output_dir / "env"

    @property
    def env_logs_dir(self):
        return self.env_output_dir / "logs"

    @property
    def env_state_file(self):
        return self.env_output_dir / "env_state.yaml"

    @property
    def test_context_file(self):
        return self.env_output_dir / "test_context.yaml"


def load_config(root_dir, config_name, local_config_name=None, extra_configs=None,
                validate=False, profile=None):
    root_dir = Path(root_dir)
    base_file = (root_dir / config_name).resolve()
    sources = [base_file]
    with base_file.open("r", encoding="utf-8") as handle:
        config = yaml.safe_load(handle) or {}
    legacy_config = config.get("framework", {}).get("legacy_config")
    if legacy_config:
        legacy_path = (root_dir / legacy_config).resolve()
        if legacy_path.exists():
            sources.append(legacy_path)
        config = _deep_merge(
            config, _legacy_to_regress_config(_parse_legacy_config(legacy_path))
        )
    local_file = (root_dir / (local_config_name or (Path(config_name).stem + ".local.yaml"))).resolve()
    if local_file.exists():
        with local_file.open("r", encoding="utf-8") as handle:
            config = _deep_merge(config, yaml.safe_load(handle) or {})
        sources.append(local_file)
    for path in extra_configs or []:
        source = Path(path).resolve()
        with source.open("r", encoding="utf-8") as handle:
            config = _deep_merge(config, yaml.safe_load(handle) or {})
        sources.append(source)
    selected_profile = profile or ("stable" if Path(config_name).stem == "stable" else "regression")
    if validate:
        validate_config(config, selected_profile)
    return RegressionConfig(root_dir=root_dir, config=config, profile=selected_profile,
                            sources=sources)


def load_regression_config(root_dir, extra_configs=None, validate=True):
    return load_config(
        root_dir, "regress.yaml", extra_configs=extra_configs,
        validate=validate, profile="regression",
    )


def validate_profile_isolation(environment):
    """Ensure stable and regression cannot address the same persistent resources."""
    root = environment.root_dir
    if environment.profile == "stable":
        regress = load_config(root, "regress.yaml", validate=True, profile="regression").config
        stable = environment.config
    else:
        regress = environment.config
        stable = load_config(root, "stable.yaml", validate=True, profile="stable").config
    errors = isolation_errors(root, regress, stable)
    if errors:
        raise ConfigurationError("environment isolation check failed:\n- " + "\n- ".join(errors))
    return True
