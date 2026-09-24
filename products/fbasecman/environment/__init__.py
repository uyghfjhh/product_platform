"""fbasecman test-environment provider registration."""

from framework.environment import register_environment_provider
from products.fbasecman.environment.provider import FbasecmanEnvironmentProvider

register_environment_provider("fbasecman", FbasecmanEnvironmentProvider)

__all__ = ["FbasecmanEnvironmentProvider"]
