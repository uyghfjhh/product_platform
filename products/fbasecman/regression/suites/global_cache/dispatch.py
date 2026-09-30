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
def _execute_started_case(context, runner, assertion):
    ops = context.ops
    ops.start_fbasecman()
    before_state = ops.capture_console_state("before")
    ops.summary["before_stats"] = before_state["stats"]
    runner(context)
    after_state = ops.capture_console_state("after")
    ops.summary["after_stats"] = after_state["stats"]
    assertion(context, before_state, after_state)


COMPOSITE_PHASE_NAMES = {
    "basic_reuse",
    "cross_client_reuse",
    "unnamed_statement_overwrite_unref",
    "named_conflict_after_global_hit_keeps_old_entry",
    "guc_set_report_bypass_response_stable",
    "guc_reset_all_bypass",
}


@contextmanager
def _case_phase(context, name, **overrides):
    """Run a sub-scenario with its own asset/config identity inside one report case."""
    ops = context.ops
    if name not in COMPOSITE_PHASE_NAMES:
        raise GlobalCacheFailure("unknown composite phase: %s" % name)
    original = ops.case
    phase = copy(original)
    phase.name = name
    for key, value in overrides.items():
        setattr(phase, key, value)
    ops.case = phase
    try:
        yield
    finally:
        ops.case = original


def _collect_phase_checks(context, phase_title, action, collected):
    ops = context.ops
    ops.summary.pop("verification_checks", None)
    first_step = len(ops.step_records)
    action()
    for step in ops.step_records[first_step:]:
        step["title"] = "%s: %s" % (phase_title, step.get("title", "未命名测试步骤"))
    for check in ops.summary.pop("verification_checks", []):
        item = dict(check)
        item["title"] = "%s: %s" % (phase_title, item.get("title", "未命名检测项"))
        collected.append(item)


def _execute_reuse_scenarios(context):
    ops = context.ops
    ops.start_fbasecman()
    before_state = ops.capture_console_state("before")
    ops.summary["before_stats"] = before_state["stats"]
    checks = []

    def run_single_connection():
        _run_jdbc_case(context)
        after = ops.capture_console_state("after_single_connection")
        _assert_basic_reuse(context, before_state, after)

    with _case_phase(
        context,
        "basic_reuse",
        sql={
            "tag": "gc_basic_reuse",
            "statement": "select name from test where id = ? /* gc_basic_reuse */",
        },
    ):
        _collect_phase_checks(context, "单连接复用", run_single_connection, checks)

    cross_before = ops.capture_console_state("before_cross_client")

    def run_cross_client():
        _run_jdbc_case(context)
        after = ops.capture_console_state("after")
        ops.summary["after_stats"] = after["stats"]
        _assert_cross_client_reuse(context, cross_before, after)

    with _case_phase(
        context,
        "cross_client_reuse",
        sql={
            "tag": "gc_cross_client_reuse",
            "statement": "select name from test where id = ? /* gc_cross_client_reuse */",
        },
    ):
        _collect_phase_checks(context, "跨客户端复用", run_cross_client, checks)
    ops.summary["verification_checks"] = checks


def _execute_statement_lifecycle_scenarios(context):
    ops = context.ops
    ops.start_fbasecman()
    before_state = ops.capture_console_state("before")
    ops.summary["before_stats"] = before_state["stats"]
    checks = []

    with _case_phase(context, "unnamed_statement_overwrite_unref"):
        _collect_phase_checks(
            context,
            "unnamed statement 覆盖",
            lambda: _run_unnamed_overwrite_case(context, before_state),
            checks,
        )

    named_before = ops.capture_console_state("before_named_conflict")

    def run_named_conflict():
        _run_libpq_case(context)
        after = ops.capture_console_state("after_named_conflict")
        _assert_named_conflict_keeps_old(context, named_before, after)

    with _case_phase(context, "named_conflict_after_global_hit_keeps_old_entry"):
        _collect_phase_checks(context, "named statement 冲突", run_named_conflict, checks)

    ops.summary["verification_checks"] = checks


def _execute_guc_bypass_scenarios(context):
    ops = context.ops
    ops.start_fbasecman()
    before_state = ops.capture_console_state("before")
    ops.summary["before_stats"] = before_state["stats"]
    checks = []

    def run_set_report():
        _run_guc_set_report_case(context)
        after = ops.capture_console_state("after_guc_set")
        _assert_guc_set_report_bypass(context, before_state, after)

    with _case_phase(context, "guc_set_report_bypass_response_stable"):
        _collect_phase_checks(context, "GUC SET report bypass", run_set_report, checks)

    reset_before = ops.capture_console_state("before_guc_reset_all")

    def run_reset_all():
        _run_guc_reset_all_bypass_case(context)
        after = ops.capture_console_state("after")
        ops.summary["after_stats"] = after["stats"]
        _assert_guc_reset_all_bypass(context, reset_before, after)

    with _case_phase(context, "guc_reset_all_bypass"):
        _collect_phase_checks(context, "GUC RESET ALL bypass", run_reset_all, checks)
    ops.summary["verification_checks"] = checks


def _execute_global_capacity_reload(context):
    ops = context.ops
    _run_capacity_reload_shrink_case(context)
    try:
        after_state = ops.capture_console_state("after", include_server=False)
    except Exception:
        _collect_capacity_shrink_failure_context(context)
        raise
    ops.summary["after_stats"] = after_state["stats"]
    before_state = {"global": [], "server": [], "stats": ops.summary.get("before_stats", {})}
    _assert_capacity_reload_shrink(context, before_state, after_state)


def _execute_close_unref(context):
    ops = context.ops
    after_state = _run_close_unref_case(context)
    ops.summary["after_stats"] = after_state["stats"]


def _execute_shared_disconnect_reuse(context):
    ops = context.ops
    after_state = _run_shared_global_entry_disconnect_one_client_reuse_case(context)
    ops.summary["after_stats"] = after_state["stats"]


def _execute_guc_reload(context):
    ops = context.ops
    before_reload_state, after_state = _run_guc_reload_toggle_case(context)
    ops.summary["after_stats"] = after_state["stats"]
    _assert_guc_reload_toggle(context, before_reload_state, after_state)


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


def _execute_case(context):
    ops = context.ops
    name = ops.case.name
    if name in STARTED_CASE_EXECUTORS:
        runner, assertion = STARTED_CASE_EXECUTORS[name]
        _execute_started_case(context, runner, assertion)
    else:
        try:
            executor = SPECIAL_CASE_EXECUTORS[name]
        except KeyError:
            raise GlobalCacheFailure("%s is not registered in CASE_EXECUTORS" % ops.case.target)
        executor(context)
    _assert_verification_checks_clean(context)
    _assert_fbasecman_no_warning_or_error(context)
