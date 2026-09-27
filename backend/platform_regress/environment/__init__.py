"""Test-environment lifecycle contracts."""

from platform_regress.environment.provider import EnvironmentProvider
from platform_regress.environment.registry import (
    create_environment_provider, register_environment_provider,
)
from platform_regress.environment.sanitizer import preflight_health_check

__all__ = [
    "EnvironmentProvider", "create_environment_provider",
    "preflight_health_check", "register_environment_provider",
]
