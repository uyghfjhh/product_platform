"""Global prepared-statement cache case dispatch (no suite runner dependency).

The platform engine path imports this module directly; ``suite.py`` re-exports
these names so the manual ``run.sh`` entry and vendored unit tests keep their
existing import points.
"""

from contextlib import contextmanager
from copy import copy

from suites.global_cache.errors import GlobalCacheFailure
from suites.global_cache.reporting import (
    _collect_capacity_shrink_failure_context,
)
from suites.global_cache.domains.guc_sequences import (
    run_guc_reset_all_bypass_case as _run_guc_reset_all_bypass_case,
    run_guc_set_report_case as _run_guc_set_report_case,
)
from suites.global_cache.domains.guc_checks import (
    assert_guc_reload_toggle as _assert_guc_reload_toggle,
)
from suites.global_cache.domains.guc_reload import (
    run_guc_reload_toggle_case as _run_guc_reload_toggle_case,
)
from suites.global_cache.domains.capacity_reload import (
    assert_capacity_reload_shrink as _assert_capacity_reload_shrink,
    run_capacity_reload_shrink_case as _run_capacity_reload_shrink_case,
)
from suites.global_cache.domains.capacity_scenarios import (
    run_capacity_eviction_zero_ref_case as _run_capacity_eviction_zero_ref_case,
    run_capacity_mixed_bypass_response_and_zero_ref_shortage_case as _run_capacity_mixed_bypass_response_and_zero_ref_shortage_case,
    run_ref_count_protects_active_entries_case as _run_ref_count_protects_active_entries_case,
)
from suites.global_cache.domains.common_assertions import (
    _assert_fbasecman_no_warning_or_error,
    _assert_verification_checks_clean,
)
from suites.global_cache.domains.driver_cases import (
    _run_jdbc_case,
    _run_libpq_case,
    _assert_libpq_activity,
    _assert_named_conflict_keeps_old,
)
from suites.global_cache.domains.reuse_scenarios import (
    _assert_basic_reuse,
    _assert_cross_client_reuse,
    _run_shared_global_entry_disconnect_one_client_reuse_case,
    _run_close_unref_case,
)
from suites.global_cache.domains.eviction_scenarios import (
    _run_backend_global_split_eviction_case,
    _run_discard_all_clears_backend_cache_case,
    _run_unnamed_overwrite_case,
    _assert_parse_invalid_error_recovery,
)
from suites.global_cache.domains.heartbeat_scenarios import (
    _run_heartbeat_rule_precedence_case,
    _run_same_sql_different_users_isolation_case,
    _assert_guc_set_report_bypass,
    _assert_guc_reset_all_bypass,
    _run_heartbeat_reclassify_case,
)


def _execute_started_case(rt, runner, assertion):
    rt.start_fbasecman()
    before_state = rt.capture_console_state("before")
    rt.summary["before_stats"] = before_state["stats"]
    runner(rt)
    after_state = rt.capture_console_state("after")
    rt.summary["after_stats"] = after_state["stats"]
    assertion(rt, before_state, after_state)


COMPOSITE_PHASE_NAMES = {
    "basic_reuse",
    "cross_client_reuse",
    "unnamed_statement_overwrite_unref",
    "named_conflict_after_global_hit_keeps_old_entry",
    "guc_set_report_bypass_response_stable",
    "guc_reset_all_bypass",
}


@contextmanager
def _case_phase(rt, name, **overrides):
    """Run a sub-scenario with its own asset/config identity inside one report case."""
    if name not in COMPOSITE_PHASE_NAMES:
        raise GlobalCacheFailure("unknown composite phase: %s" % name)
    original = rt.case
    phase = copy(original)
    phase.name = name
    for key, value in overrides.items():
        setattr(phase, key, value)
    rt.case = phase
    try:
        yield
    finally:
        rt.case = original


def _collect_phase_checks(rt, phase_title, action, collected):
    rt.summary.pop("verification_checks", None)
    first_step = len(rt.step_records)
    action()
    for step in rt.step_records[first_step:]:
        step["title"] = "%s: %s" % (phase_title, step.get("title", "未命名测试步骤"))
    for check in rt.summary.pop("verification_checks", []):
        item = dict(check)
        item["title"] = "%s: %s" % (phase_title, item.get("title", "未命名检测项"))
        collected.append(item)


def _execute_reuse_scenarios(rt):
    rt.start_fbasecman()
    before_state = rt.capture_console_state("before")
    rt.summary["before_stats"] = before_state["stats"]
    checks = []

    def run_single_connection():
        _run_jdbc_case(rt)
        after = rt.capture_console_state("after_single_connection")
        _assert_basic_reuse(rt, before_state, after)

    with _case_phase(
        rt,
        "basic_reuse",
        sql={
            "tag": "gc_basic_reuse",
            "statement": "select name from test where id = ? /* gc_basic_reuse */",
        },
    ):
        _collect_phase_checks(rt, "单连接复用", run_single_connection, checks)

    cross_before = rt.capture_console_state("before_cross_client")

    def run_cross_client():
        _run_jdbc_case(rt)
        after = rt.capture_console_state("after")
        rt.summary["after_stats"] = after["stats"]
        _assert_cross_client_reuse(rt, cross_before, after)

    with _case_phase(
        rt,
        "cross_client_reuse",
        sql={
            "tag": "gc_cross_client_reuse",
            "statement": "select name from test where id = ? /* gc_cross_client_reuse */",
        },
    ):
        _collect_phase_checks(rt, "跨客户端复用", run_cross_client, checks)
    rt.summary["verification_checks"] = checks


def _execute_statement_lifecycle_scenarios(rt):
    rt.start_fbasecman()
    before_state = rt.capture_console_state("before")
    rt.summary["before_stats"] = before_state["stats"]
    checks = []

    with _case_phase(rt, "unnamed_statement_overwrite_unref"):
        _collect_phase_checks(
            rt,
            "unnamed statement 覆盖",
            lambda: _run_unnamed_overwrite_case(rt, before_state),
            checks,
        )

    named_before = rt.capture_console_state("before_named_conflict")

    def run_named_conflict():
        _run_libpq_case(rt)
        after = rt.capture_console_state("after_named_conflict")
        _assert_named_conflict_keeps_old(rt, named_before, after)

    with _case_phase(rt, "named_conflict_after_global_hit_keeps_old_entry"):
        _collect_phase_checks(rt, "named statement 冲突", run_named_conflict, checks)

    rt.summary["verification_checks"] = checks


def _execute_guc_bypass_scenarios(rt):
    rt.start_fbasecman()
    before_state = rt.capture_console_state("before")
    rt.summary["before_stats"] = before_state["stats"]
    checks = []

    def run_set_report():
        _run_guc_set_report_case(rt)
        after = rt.capture_console_state("after_guc_set")
        _assert_guc_set_report_bypass(rt, before_state, after)

    with _case_phase(rt, "guc_set_report_bypass_response_stable"):
        _collect_phase_checks(rt, "GUC SET report bypass", run_set_report, checks)

    reset_before = rt.capture_console_state("before_guc_reset_all")

    def run_reset_all():
        _run_guc_reset_all_bypass_case(rt)
        after = rt.capture_console_state("after")
        rt.summary["after_stats"] = after["stats"]
        _assert_guc_reset_all_bypass(rt, reset_before, after)

    with _case_phase(rt, "guc_reset_all_bypass"):
        _collect_phase_checks(rt, "GUC RESET ALL bypass", run_reset_all, checks)
    rt.summary["verification_checks"] = checks


def _execute_global_capacity_reload(rt):
    _run_capacity_reload_shrink_case(rt)
    try:
        after_state = rt.capture_console_state("after", include_server=False)
    except Exception:
        _collect_capacity_shrink_failure_context(rt)
        raise
    rt.summary["after_stats"] = after_state["stats"]
    before_state = {"global": [], "server": [], "stats": rt.summary.get("before_stats", {})}
    _assert_capacity_reload_shrink(rt, before_state, after_state)


def _execute_close_unref(rt):
    after_state = _run_close_unref_case(rt)
    rt.summary["after_stats"] = after_state["stats"]


def _execute_shared_disconnect_reuse(rt):
    after_state = _run_shared_global_entry_disconnect_one_client_reuse_case(rt)
    rt.summary["after_stats"] = after_state["stats"]


def _execute_guc_reload(rt):
    before_reload_state, after_state = _run_guc_reload_toggle_case(rt)
    rt.summary["after_stats"] = after_state["stats"]
    _assert_guc_reload_toggle(rt, before_reload_state, after_state)


STARTED_CASE_EXECUTORS = {
    "prepare_before_bind_deploy": (_run_libpq_case, _assert_libpq_activity),
    "bypass_prepare_protocol_sequence": (_run_libpq_case, _assert_libpq_activity),
    "parse_invalid_error_recovery_same_connection": (
        _run_jdbc_case, _assert_parse_invalid_error_recovery,
    ),
}


SPECIAL_CASE_EXECUTORS = {
    "reuse_single_and_cross_client": _execute_reuse_scenarios,
    "close_and_disconnect_unref": _execute_close_unref,
    "capacity_eviction_zero_ref": _run_capacity_eviction_zero_ref_case,
    "capacity_mixed_bypass_response_and_zero_ref_shortage": (
        _run_capacity_mixed_bypass_response_and_zero_ref_shortage_case
    ),
    "ref_count_protects_active_entries": _run_ref_count_protects_active_entries_case,
    "statement_mapping_lifecycle": _execute_statement_lifecycle_scenarios,
    "backend_global_split_eviction": _run_backend_global_split_eviction_case,
    "discard_all_clears_backend_cache": _run_discard_all_clears_backend_cache_case,
    "shared_global_entry_disconnect_one_client_other_client_reuse_still_ok": (
        _execute_shared_disconnect_reuse
    ),
    "reload_enable_guc_sync_existing_entry": _execute_guc_reload,
    "guc_bypass_set_and_reset": _execute_guc_bypass_scenarios,
    "heartbeat_reload_reclassifies_existing_normal_entry": _run_heartbeat_reclassify_case,
    "heartbeat_rule_precedence_over_global": _run_heartbeat_rule_precedence_case,
    "same_sql_different_users_isolation": _run_same_sql_different_users_isolation_case,
    "global_capacity_reload_shrink": _execute_global_capacity_reload,
}


def _execute_case(rt):
    name = rt.case.name
    if name in STARTED_CASE_EXECUTORS:
        runner, assertion = STARTED_CASE_EXECUTORS[name]
        _execute_started_case(rt, runner, assertion)
    else:
        try:
            executor = SPECIAL_CASE_EXECUTORS[name]
        except KeyError:
            raise GlobalCacheFailure("%s is not registered in CASE_EXECUTORS" % rt.case.target)
        executor(rt)
    _assert_verification_checks_clean(rt)
    _assert_fbasecman_no_warning_or_error(rt)
