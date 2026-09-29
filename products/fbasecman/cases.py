"""fbasecman regression targets registered with the platform engine.

Every catalog target is registered as a platform ``RegressionCase``. Rewritten
cases use platform SDK primitives directly; suites still being rewritten bind
product runtimes and executors through the platform ``RuntimeExecutorCase``.
"""

import json
from pathlib import Path
from products.fbasecman.native import (HeartbeatBindCase, SavepointRecoveryCase,
                                       ReloadDisableMonitorRouteLossCase,
                                       OutstandingConsistencyCase, RwToggleCase,
                                       SqlParseExtendedProtocolCase,
                                       JdbcConsoleHaCommandsCase, guc_case)
from products.fbasecman.native import (SetNodeWriteIdempotentCase, IdempotentHaCommandCase,
                                        SetNodeWeightIdempotentCase)
from products.fbasecman.ha_native import HaCommandsCase
from products.fbasecman.common_native import CommonCase
from products.fbasecman.runtime_cases import runtime_case


PRODUCT_ROOT = Path(__file__).parent
REPO_ROOT = PRODUCT_ROOT.parent.parent
DEFAULT_LEGACY_ROOT = PRODUCT_ROOT / "regression"
CATALOG = json.loads((PRODUCT_ROOT / "regression" / "catalog.json").read_text(encoding="utf-8"))
if CATALOG.get("schema_version") != 1:
    raise ValueError("fbasecman 用例目录版本无效")
CASE_METADATA = CATALOG["cases"]


_MIGRATING_SUITES = ("ha_commands", "high_availability", "handover",
                     "global_cache")


def _suite_specs(source):
    """Catalog specs straight from suite manifests — no registry/plugin
    modules (they import ``suite.py``/the legacy runner, which the platform
    path must not touch)."""
    import importlib

    from products.fbasecman.runtime_cases import (
        _ensure_imports, _suite_case_items)
    _ensure_imports(source)
    specs = {}
    for suite_id in _MIGRATING_SUITES:
        manifest = importlib.import_module("suites.%s.manifest" % suite_id)
        for spec in _suite_case_items(manifest, suite_id):
            specs[spec.target] = spec
    return specs


NATIVE_CASES = {
    **{
        "common." + name: CommonCase(name)
        for name in (
            "console_commands", "err_logger_rotation",
            "route_stats_quantiles", "worker_thread_lifecycle",
        )
    },
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
    "ha_commands.refresh_cluster": HaCommandsCase("refresh_cluster"),
    "ha_commands.refresh_cluster_probe_edges": HaCommandsCase("refresh_cluster_probe_edges"),
    "ha_commands.refresh_cluster_syntax_errors": HaCommandsCase("refresh_cluster_syntax_errors"),
    "ha_commands.set_cluster_30_datasource_roundtrip": HaCommandsCase("set_cluster_30_datasource_roundtrip"),
    "ha_commands.set_cluster_invalid_commands": HaCommandsCase("set_cluster_invalid_commands"),
    "ha_commands.console_set_validation_toggle": HaCommandsCase("console_set_validation_toggle"),
    "ha_commands.set_cluster_parted_active_roundtrip": HaCommandsCase("set_cluster_parted_active_roundtrip"),
    "ha_commands.set_cluster_write_promoted_roundtrip": HaCommandsCase("set_cluster_write_promoted_roundtrip"),
    "ha_commands.set_node_promoted_write_cluster_conflict": HaCommandsCase("set_node_promoted_write_cluster_conflict"),
    "ha_commands.write_cluster_format_preservation": HaCommandsCase("write_cluster_format_preservation"),
    "tmp.reload_disable_monitor_route_loss": ReloadDisableMonitorRouteLossCase(),
    **{
        "guc." + name: guc_case(name)
        for name in (
            "search_path_reuse_sql_parse", "search_path_reuse_hint",
            "search_path_multivalue_sql_parse", "search_path_multivalue_hint",
            "search_path_empty_normalize", "search_path_mixed_quotes_cleanup",
            "reset_param_sql_parse", "reset_param_hint",
            "reset_all_sql_parse", "reset_all_hint",
            "discard_all_sql_parse", "discard_all_hint",
            "set_local_transaction_sql_parse", "set_local_transaction_hint",
            "case_insensitive_quotes_sql_parse", "case_insensitive_quotes_hint",
            "report_param_timezone_sql_parse", "report_param_timezone_hint",
        )
    },
    **{
        "outstanding." + name: OutstandingConsistencyCase(mode)
        for name, mode in (
            ("lru_close_multiple_restore_consistency", "lru_close_multiple_restore"),
            ("lru_close_skipped_restore_consistency", "lru_close_skipped_restore"),
            ("lru_close_success_consistency", "lru_close_success"),
            ("lru_confirmed_multiple_restore_order", "lru_confirmed_multiple_restore_order"),
            ("long_statement_name_cleanup", "long_statement_name_cleanup"),
            ("fragmented_close_packet", "fragmented_close_packet"),
            ("fragmented_execute_packet", "fragmented_execute_packet"),
            ("execute_payload_validation", "execute_payload_validation"),
            ("parse_failure_single_consistency", "parse_failure_single"),
            ("parse_failure_shared_sync_consistency", "parse_failure_shared_sync"),
            ("execute_failure_shared_sync_consistency", "execute_failure_shared_sync"),
        )
    },
    **{
        "rw_toggle." + name: RwToggleCase(topology, route_mode, driver, scenario)
        for name, topology, route_mode, driver, scenario in (
            ("mmr_hint_switch", "mmr", "hint", "psql", "switch"),
            ("mmr_hint_write", "mmr", "hint", "psql", "write"),
            ("mmr_hint_read", "mmr", "hint", "psql", "read"),
            ("mmr_hint_jdbc", "mmr", "hint", "jdbc", "jdbc"),
            ("rep_hint_switch", "replication", "hint", "psql", "switch"),
            ("rep_hint_read", "replication", "hint", "psql", "read"),
            ("rep_hint_write", "replication", "hint", "psql", "write"),
            ("rep_hint_jdbc", "replication", "hint", "jdbc", "jdbc"),
            ("rep_read_port", "replication", "port", "psql", "read"),
            ("rep_write_port", "replication", "port", "psql", "write"),
            ("rep_port_jdbc", "replication", "port", "jdbc", "jdbc"),
            ("mmr_read_port", "mmr", "port", "psql", "read"),
            ("mmr_write_port", "mmr", "port", "psql", "write"),
            ("mmr_port_jdbc", "mmr", "port", "jdbc", "jdbc"),
        )
    },
}

# Suite manifests are static: they import cleanly before any environment
# override exists (config values only matter when load_regression_config is
# called, which reads _EXTRA_CONFIGS lazily).
_LEGACY_SPECS = _suite_specs(DEFAULT_LEGACY_ROOT)


def _migrating_case(item):
    suite_id = item["target"].partition(".")[0]
    spec = _LEGACY_SPECS.get(item["target"])
    return runtime_case(item, spec)


CASES = {item["target"]: NATIVE_CASES.get(
    item["target"], _migrating_case(item)) for item in CASE_METADATA}
CASE_ORDER = [item["target"] for item in CASE_METADATA]
