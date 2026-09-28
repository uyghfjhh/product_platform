"""fbasecman regression targets registered with the platform engine.

Legacy suite cases execute in-process through ``suites.<id>.suite.run_case``
inside the platform ``RegressionEngine``; the suite still owns fixtures,
assertions and report artifacts, while the engine owns scheduling, verdict
mapping and evidence collection.
"""

import importlib
import json
import sys
from pathlib import Path

from platform_regress import Blocked
from platform_regress.suites.legacy import LegacyCaseBinding, LegacySuiteCase
from products.fbasecman.native import (HeartbeatBindCase, SavepointRecoveryCase,
                                        SqlParseExtendedProtocolCase,
                                        JdbcConsoleHaCommandsCase)
from products.fbasecman.native import (SetNodeWriteIdempotentCase, IdempotentHaCommandCase,
                                        SetNodeWeightIdempotentCase)


PRODUCT_ROOT = Path(__file__).parent
REPO_ROOT = PRODUCT_ROOT.parent.parent
DEFAULT_LEGACY_ROOT = PRODUCT_ROOT / "regression" / "legacy"
CATALOG = json.loads((PRODUCT_ROOT / "regression" / "catalog.json").read_text(encoding="utf-8"))
if CATALOG.get("schema_version") != 1:
    raise ValueError("fbasecman 用例目录版本无效")
CASE_METADATA = CATALOG["cases"]

# Extra config files merged into every regression-config load.  The platform
# supplies the current environment's override through the case context; the
# list is evaluated at call time so one process can retarget environments.
_EXTRA_CONFIGS = []
_LOADER_PROFILED = False


def _ensure_imports(source):
    """Isolate the legacy suite import path and install the override loader."""
    global _LOADER_PROFILED
    for path in (REPO_ROOT, REPO_ROOT / "backend", source):
        value = str(path)
        while value in sys.path:
            sys.path.remove(value)
        sys.path.insert(0, value)
    import cmanconf

    if _LOADER_PROFILED:
        return
    original = cmanconf.load_regression_config

    def load_with_profile(root_dir, extra_configs=None, validate=True):
        extras = list(extra_configs or []) + list(_EXTRA_CONFIGS)
        return original(root_dir, extra_configs=extras, validate=validate)

    cmanconf.load_regression_config = load_with_profile
    _LOADER_PROFILED = True


def _load_registry(source):
    """Import the suite registry after the override loader is installed."""
    _ensure_imports(source)
    from suites.registry import get_default_registry
    return get_default_registry()


def _suite_specs(source):
    registry = _load_registry(source)
    specs = {}
    for plugin in registry.all_suites():
        for spec in plugin.get_cases():
            specs[spec.target] = spec
    return specs


class LegacyCmanCase(LegacySuiteCase):
    """fbasecman suite case executed by its own ``run_case`` in-process."""

    def __init__(self, target, metadata, default_enabled=True):
        super().__init__(
            target, self._resolve,
            summary=metadata.get("summary") or "",
            default_enabled=default_enabled,
            # The handover suite serializes through output/handover.lock.
            lock_name="handover" if target.startswith("handover.") else None,
        )
        self._metadata = metadata

    def _resolve(self, context):
        environment = context.environment or {}
        source = Path(environment.get("legacy_source")
                      or DEFAULT_LEGACY_ROOT).resolve()
        override_value = environment.get("legacy_override")
        report_value = environment.get("legacy_report_root")
        if not override_value or not report_value:
            raise Blocked("缺少当前环境的 fbasecman 测试配置或报告目录")
        override = Path(override_value).resolve()
        if not (source / "suites" / "registry.py").is_file() or not override.is_file():
            raise Blocked("fbasecman 用例来源或环境覆盖配置不存在")
        _EXTRA_CONFIGS[:] = [override]
        registry = _load_registry(source)
        suite_id, _, name = self.target.partition(".")
        plugin = registry.get(suite_id)
        if plugin is None:
            raise Blocked("未注册的 fbasecman 套件: %s" % suite_id)
        spec = next((item for item in plugin.get_cases() if item.name == name), None)
        if spec is None:
            raise Blocked("套件 %s 没有用例 %s" % (suite_id, name))
        import cmanconf as configuration
        environment_cfg = configuration.load_regression_config(source)
        configuration.validate_profile_isolation(environment_cfg)
        suite_module = importlib.import_module("suites.%s.suite" % suite_id)
        run_case = getattr(suite_module, "run_case", None)
        if run_case is None:
            raise Blocked("套件 %s 尚未接入平台执行路径" % suite_id)

        def run_root(item):
            return (environment_cfg.output_dir / "runs" /
                    item.suite_id / item.name)

        return LegacyCaseBinding(spec, run_case, source, run_root=run_root)


NATIVE_CASES = {
    "sql_parse.savepoint_recovery_after_local_25p02": SavepointRecoveryCase(),
    "sql_parse.heartbeat_bind_normal": HeartbeatBindCase("normal"),
    "sql_parse.heartbeat_bind_invalid": HeartbeatBindCase("malformed"),
    "sql_parse.heartbeat_bind_unsupported": HeartbeatBindCase("binary"),
    "ha_commands.sql_parse_extended_protocol": SqlParseExtendedProtocolCase(),
    "ha_commands.jdbc_console_ha_commands": JdbcConsoleHaCommandsCase(),
    "ha_commands.set_node_write_idempotent": SetNodeWriteIdempotentCase(),
    "ha_commands.set_node_promoted_idempotent": IdempotentHaCommandCase(
        "SET NODE PROMOTED pg_1 IN GROUP mmr_group;", ("pg_cluster_1",),
        ("mmr_group", "active", "pg_cluster_1"), "核对 SET NODE PROMOTED 幂等命令"),
    "ha_commands.set_cluster_active_idempotent": IdempotentHaCommandCase(
        "SET CLUSTER ACTIVE pg_cluster_1;", ("pg_cluster_1", "VALID", "pg_1"),
        ("mmr_group", "pg_cluster_1", "pg_1"), "核对 SET CLUSTER ACTIVE 幂等命令",
        initial_query="SHOW CLUSTERS;"),
    "ha_commands.set_node_weight_idempotent": SetNodeWeightIdempotentCase(),
}

# Suite manifests are static: they import cleanly before any environment
# override exists (config values only matter when load_regression_config is
# called, which reads _EXTRA_CONFIGS lazily).
_LEGACY_SPECS = _suite_specs(DEFAULT_LEGACY_ROOT)


def _legacy_case(item):
    spec = _LEGACY_SPECS.get(item["target"])
    enabled = item.get("enabled", True)
    if spec is not None:
        enabled = enabled and getattr(spec, "enabled", True) \
            and not getattr(spec, "long_time", False)
    return LegacyCmanCase(item["target"], item, default_enabled=bool(enabled))


CASES = {item["target"]: NATIVE_CASES.get(
    item["target"], _legacy_case(item)) for item in CASE_METADATA}
CASE_ORDER = [item["target"] for item in CASE_METADATA]
