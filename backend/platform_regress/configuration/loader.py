"""Layered YAML configuration and standard artifact paths."""

import re
from pathlib import Path

import yaml

from .validation import ConfigurationError


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
    def runtime_dir(self):
        return Path(self.config["framework"]["runtime_dir"])

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
                validate=False, profile=None, validator=None, legacy_mapper=None):
    root_dir = Path(root_dir)
    base_file = (root_dir / config_name).resolve()
    sources = [base_file]
    with base_file.open("r", encoding="utf-8") as handle:
        config = yaml.safe_load(handle) or {}
    legacy_config = config.get("framework", {}).get("legacy_config")
    if legacy_config and legacy_mapper is not None:
        legacy_path = (root_dir / legacy_config).resolve()
        if legacy_path.exists():
            sources.append(legacy_path)
        config = _deep_merge(
            config, legacy_mapper(_parse_legacy_config(legacy_path))
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
        if validator is None:
            raise ConfigurationError(
                "validate=True requires a validator callable for this profile"
            )
        validator(config, selected_profile)
    return RegressionConfig(root_dir=root_dir, config=config, profile=selected_profile,
                            sources=sources)


def load_regression_config(root_dir, extra_configs=None, validate=True,
                           validator=None, legacy_mapper=None):
    return load_config(
        root_dir, "regress.yaml", extra_configs=extra_configs,
        validate=validate, profile="regression",
        validator=validator, legacy_mapper=legacy_mapper,
    )


def validate_profile_isolation(environment, loader=None, isolation_check=None):
    """Ensure stable and regression cannot address the same persistent resources."""
    if loader is None:
        loader = load_config
    root = environment.root_dir
    if environment.profile == "stable":
        regress = loader(root, "regress.yaml", validate=True, profile="regression").config
        stable = environment.config
    else:
        regress = environment.config
        stable = loader(root, "stable.yaml", validate=True, profile="stable").config
    errors = isolation_check(root, regress, stable) if isolation_check is not None else []
    if errors:
        raise ConfigurationError("environment isolation check failed:\n- " + "\n- ".join(errors))
    return True
