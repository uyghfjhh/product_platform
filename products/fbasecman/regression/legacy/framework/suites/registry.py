"""Compatibility import; suite registry lives in the platform SDK."""

import platform_regress.suites.registry as _registry
from platform_regress.suites.registry import SuiteRegistry  # noqa: F401


def _preflight(root_dir):
    from framework.environment.sanitizer import preflight_health_check
    return preflight_health_check(root_dir, auto_heal=True)


_registry.set_default_preflight_check(_preflight)
_registry.set_default_quiet_env_var("FBASECMAN_QUIET_ENV")
