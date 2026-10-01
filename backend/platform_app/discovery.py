from platform_regress.sdk import CaseCatalog

from .config import Settings
from .providers import provider_for
from .providers import validate_target as provider_validate_target


def discover_cases(settings: Settings, product_id: str) -> list[dict]:
    provider = provider_for(settings, product_id)
    return CaseCatalog(provider.discover(settings)).to_api()


def validate_target(settings: Settings, product_id: str, target: str) -> bool:
    return provider_validate_target(settings, product_id, target)
