"""Validated installation and recording of reload configuration."""
from platform_regress.configuration.reload import (  # noqa: F401
    ReloadConfigError, config_lines_by_keys, has_config_value,
    install_reload_config, record_config_transition,
)
