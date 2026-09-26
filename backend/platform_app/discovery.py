from .config import Settings
from .product_registry import get_product_spec
from .providers import PROVIDERS
from .providers import validate_target as provider_validate_target


def discover_cases(settings: Settings, product_id: str) -> list[dict]:
    if get_product_spec(product_id) is None:
        raise ValueError("未知产品")
    provider = PROVIDERS.get(product_id)
    if provider is None:
        raise ValueError("产品未注册提供者: %s" % product_id)
    return provider.discover(settings)


def validate_target(settings: Settings, product_id: str, target: str) -> bool:
    if get_product_spec(product_id) is None:
        raise ValueError("未知产品")
    return provider_validate_target(settings, product_id, target)
