"""Compatibility import; environment provider registry lives in the platform SDK."""
import platform_regress.environment.registry as _impl
from platform_regress.environment.registry import (  # noqa: F401
    create_environment_provider, register_environment_provider,
)

_PROVIDERS = _impl._PROVIDERS
