import importlib.util
import sys
from pathlib import Path

from platform_regress.sdk import CaseContext, RegressionEngine


TARGET = "global_cache.reuse_single_and_cross_client"
LONG_TIME_TARGETS = {
    "handover.console_server_lifecycle_statistics",
    "handover.console_client_statistics",
    "handover.console_pgbench_mmr_hint_statistics",
    "handover.console_pgbench_mmr_port_statistics",
    "handover.console_pgbench_rep_hint_statistics",
    "handover.console_pgbench_balance_statistics",
}


def load_cases():
    path = Path(__file__).parents[1] / "products" / "fbasecman" / "cases.py"
    spec = importlib.util.spec_from_file_location("cman_cases_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _engine_run(case, tmp_path):
    context = CaseContext(TARGET, tmp_path / "output", environment={})
    return RegressionEngine().run(case, context), context


def test_catalog_registers_all_targets():
    module = load_cases()
    assert len(module.CASES) == len(module.CASE_METADATA) == 212


def test_long_time_cases_skip_suite_runs():
    module = load_cases()
    disabled = {target for target, case in module.CASES.items()
                if not getattr(case, "default_enabled", True)}
    assert disabled == LONG_TIME_TARGETS


def test_resolve_blocked_without_environment(tmp_path):
    module = load_cases()
    case = module.CASES[TARGET]
    result, _ = _engine_run(case, tmp_path)
    assert result.verdict == "BLOCKED"


def test_savepoint_protocol_case_is_native():
    module = load_cases()
    case = module.CASES["sql_parse.savepoint_recovery_after_local_25p02"]
    assert type(case).__name__ == "SavepointRecoveryCase"


def test_jdbc_console_case_is_native():
    module = load_cases()
    assert type(module.CASES["ha_commands.jdbc_console_ha_commands"]).__name__ == "JdbcConsoleHaCommandsCase"


def test_set_node_write_idempotent_case_is_native():
    module = load_cases()
    assert type(module.CASES["ha_commands.set_node_write_idempotent"]).__name__ == "SetNodeWriteIdempotentCase"


def test_more_idempotent_ha_cases_are_native():
    module = load_cases()
    assert type(module.CASES["ha_commands.set_node_promoted_idempotent"]).__name__ == "IdempotentHaCommandCase"
    assert type(module.CASES["ha_commands.set_cluster_active_idempotent"]).__name__ == "IdempotentHaCommandCase"


def test_common_cases_are_native():
    module = load_cases()
    names = (
        "console_commands", "err_logger_rotation",
        "route_stats_quantiles", "worker_thread_lifecycle",
    )
    for name in names:
        assert type(module.CASES["common." + name]).__name__ == "CommonCase"


def test_every_catalog_case_has_a_native_platform_host():
    module = load_cases()
    assert len(module.CASES) == 212
    assert not [target for target, case in module.CASES.items()
                if type(case).__name__ == "LegacyCmanCase"]
    suite_hosts = [case for case in module.CASES.values()
                   if type(case).__name__ == "SuiteNativeCase"]
    runtime_hosts = [case for case in module.CASES.values()
                     if type(case).__name__ == "RuntimeExecutorCase"]
    global_cache_hosts = [case for case in module.CASES.values()
                          if type(case).__name__ == "_GlobalCachePlatformCase"]
    assert len(suite_hosts) == 0
    assert len(runtime_hosts) + len(global_cache_hosts) == 144
    assert len(runtime_hosts) == 126
    assert len(global_cache_hosts) == 18


def test_large_suites_no_longer_use_run_case_bridge():
    module = load_cases()
    hosts = {"global_cache": "_GlobalCachePlatformCase"}
    for suite in ("global_cache", "ha_commands", "handover", "high_availability"):
        cases = [case for target, case in module.CASES.items()
                 if target.startswith(suite + ".") and target not in module.NATIVE_CASES]
        assert cases
        assert {type(case).__name__ for case in cases} == {
            hosts.get(suite, "RuntimeExecutorCase")}


def test_suite_failures_map_to_fail_verdict(tmp_path):
    from platform_regress.sdk import CaseFailure

    module = load_cases()
    for suite, failure_name in (
            ("ha_commands", "HaCommandFailure"),
            ("high_availability", "HighAvailabilityFailure"),
            ("handover", "HandoverFailure"),
            ("global_cache", "GlobalCacheFailure")):
        sys.path.insert(0, str(module.DEFAULT_REGRESS_ROOT))
        try:
            runtime_module = __import__(
                "suites.%s.%s" % (suite, "errors" if suite == "global_cache" else "runtime"),
                fromlist=[failure_name])
            failure_class = getattr(runtime_module, failure_name)
        finally:
            sys.path.remove(str(module.DEFAULT_REGRESS_ROOT))
        assert issubclass(failure_class, CaseFailure), failure_name


def test_common_table_parser_matches_legacy_psql_shape():
    from products.fbasecman.common_native import _parse_table

    output = " node_name | group_name\n-----------+------------\n pg_1      | mmr_group\n(1 row)\n"
    headers, rows = _parse_table(output)
    assert headers == ["node_name", "group_name"]
    assert rows == [{"node_name": "pg_1", "group_name": "mmr_group"}]


def test_platform_case_path_never_loads_suite_runner_or_run_case():
    """§5.0.1 architecture guard: the modules the platform engine reaches for
    resolving fbasecman cases must not import the legacy suite runner, the
    per-suite ``suite.py`` orchestration modules, the plugin registry, or
    ``run_case``.  Those entry points were physically deleted; this guard keeps
    the platform path honest if any of them ever reappears.  Run in a
    subprocess so ``sys.modules`` is pristine."""
    import subprocess

    repo = Path(__file__).parents[1]
    legacy = repo / "products" / "fbasecman" / "regression"
    code = """
import importlib, sys
sys.path[:0] = [%r, %r, %r]
for name in (
    "suites.ha_commands.manifest",
    "suites.ha_commands.dispatch",
    "suites.ha_commands.runtime",
    "suites.high_availability.manifest",
    "suites.high_availability.dispatch",
    "suites.high_availability.runtime",
    "suites.handover.manifest",
    "suites.handover.executors",
    "suites.handover.runtime",
    "suites.global_cache.manifest",
    "suites.global_cache.dispatch",
    "suites.global_cache.domains.common_assertions",
    "suites.global_cache.runtime",
    "products.fbasecman.runtime_cases",
    "products.fbasecman.cases",
):
    importlib.import_module(name)
banned = [
    "platform_regress.suites.runner",
    "suites.registry",
    "suites.ha_commands.suite",
    "suites.high_availability.suite",
    "suites.handover.suite",
    "suites.global_cache.suite",
    "suites.ha_commands.plugin",
    "suites.high_availability.plugin",
    "suites.handover.plugin",
    "suites.global_cache.plugin",
]
loaded = [name for name in banned if name in sys.modules]
assert not loaded, loaded
""" % (str(repo), str(repo / "backend"), str(legacy))
    subprocess.run([sys.executable, "-c", code], check=True)
