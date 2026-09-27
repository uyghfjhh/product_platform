import importlib
import pkgutil

from framework.errors import ConfigError


def discover_cases(package_name):
    """Discover public case modules and require each to export CASE."""
    package = importlib.import_module(package_name)
    cases = []
    for item in pkgutil.walk_packages(package.__path__, package.__name__ + "."):
        name = item.name.rsplit(".", 1)[-1]
        if item.ispkg or name.startswith("_") or name == "common":
            continue
        module = importlib.import_module(item.name)
        case = getattr(module, "CASE", None)
        if not isinstance(case, dict):
            raise ConfigError("用例模块必须导出 CASE 字典: %s" % item.name)
        cases.append(case)
    return sorted(cases, key=lambda case: case.get("id", ""))
