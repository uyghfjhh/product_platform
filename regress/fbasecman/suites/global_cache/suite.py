"""Global prepared-statement cache suite entry and remaining scenarios."""

import os
import re
import shutil
from contextlib import contextmanager
from copy import copy

from framework.evidence.assertions import stats_delta as _stats_delta
from framework.execution.phased_process import PhaseAction, observe_phases
from framework.evidence.log_checks import find_forbidden_log_patterns
from products.fbasecman.console import parse_pipe_rows
from framework.reporting import render_psql_table_from_pipe_text
from framework.configuration.reload import (
    config_lines_by_keys as _conf_lines_by_keys,
    install_reload_config as _install_reload_config,
    record_config_transition as _record_reload_conf_steps,
)
from suites.global_cache.runtime import (
    CaseRuntime,
    GlobalCacheFailure,
    VerificationFailure,
    _find_case,
    _load_env,
    _safe_name,
    _validate_report_levels,
    case_items,
    show,
)
from products.fbasecman.config import extract_config_lines as _extract_conf_lines
from suites.global_cache.reporting import (
    _collect_capacity_shrink_failure_context,
    _summary_set_log_window,
    _summary_set_pg_log_verify,
)
from suites.global_cache.result import set_report_blocks as _summary_set_report_blocks
from suites.global_cache.manifest import (
    BACKEND_PS_LIMIT_KEY,
    GLOBAL_PS_LIMIT_KEY,
    NEGATIVE_LOG_PATTERNS,
    NOISE_PATTERNS,
    formal_case_items,
)
from suites.global_cache.drivers import (
    compile_java as _compile_java,
    build_libpq_asset as _build_libpq_asset,
    jdbc_url as _jdbc_url,
    libpq_source as _driver_libpq_source,
    stage_libpq_source as _driver_stage_libpq_source,
    start_phased_libpq as _start_phased_libpq,
    run_libpq_asset as _run_libpq_asset,
    run_jdbc_asset as _run_jdbc_asset,
    run_jdbc_asset_phased as _run_jdbc_asset_phased,
    jdbc_source_file as _driver_jdbc_source_file,
    run_case_jdbc as _driver_run_case_jdbc,
    record_driver_api_calls as _record_driver_api_calls,
    libpq_prepared_operations as _libpq_prepared_operations,
)
from suites.global_cache.paths import asset_path as _global_cache_asset_path
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
from suites.global_cache.domains.capacity import (
    case_ps_limit as _case_ps_limit,
    ps_limit_replacements as _ps_limit_replacements,
)
from suites.global_cache.waits import (
    wait_target_entries_unref as _wait_target_entries_unref,
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
    _assert_negative_logs,
    _assert_fbasecman_no_warning_or_error,
    _assert_verification_checks_clean,
    _stats_change_text,
)
from suites.global_cache.domains.driver_cases import (
    _jdbc_driver_source_file,
    _run_jdbc_case,
    _libpq_driver_source,
    _copy_libpq_driver_tree,
    _append_driver_log,
    _run_libpq_case,
    _assert_libpq_activity,
    _assert_unnamed_overwrite,
    _assert_named_conflict_keeps_old,
)
from suites.global_cache.domains.reuse_scenarios import (
    _assert_basic_reuse,
    _assert_cross_client_reuse,
    _assert_close_unref,
    _assert_shared_global_entry_disconnect_one_client_reuse,
    _run_shared_global_entry_disconnect_one_client_reuse_case,
    _run_close_unref_case,
)
from suites.global_cache.domains.eviction_scenarios import (
    _assert_backend_global_split_eviction,
    _run_backend_global_split_eviction_case,
    _run_discard_all_clears_backend_cache_case,
    _run_unnamed_overwrite_case,
    _assert_discard_all_clears_backend_cache,
    _assert_parse_invalid_error_recovery,
)
from suites.global_cache.domains.heartbeat_scenarios import (
    _run_heartbeat_rule_precedence_case,
    _run_same_sql_different_users_isolation_case,
    _assert_guc_set_report_bypass,
    _assert_guc_reset_all_bypass,
    _find_rows_by_sql,
    _assert_heartbeat_reload_reclassifies_existing_normal_entry,
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


def run(root, target=None):
    _validate_report_levels()
    env, context = _load_env(root)
    if target is None:
        selected = formal_case_items()
    else:
        selected = [_find_case(target)]

    if not selected:
        raise GlobalCacheFailure("global_cache has no selected cases")

    print("----------------------global_cache---------------------------------", flush=True)

    failures = []
    success_count = 0
    for case in selected:
        rt = CaseRuntime(root, env, context, case)
        rt.trace("[global_cache] run   %s" % case.target)
        rt.trace("summary: %s" % case.summary)
        rt.trace(
            "manifest: batch=%s driver=%s topology=%s rw=%s pool=%s"
            % (
                case.batch,
                case.driver,
                case.topology,
                case.rw_split_method,
                case.pool_mode,
            )
        )
        if case.notes:
            rt.trace("notes: %s" % " | ".join(case.notes))
        try:
            _execute_case(rt)
            _assert_negative_logs(rt)
            rt.summary["status"] = "PASS"
            success_count += 1
            print("%-55s SUCCESS" % case.target, flush=True)
        except Exception as exc:
            rt.summary["status"] = "FAIL"
            issue_suffix = " [%s]" % case.issue_id if case.issue_id else ""
            rt.summary["reason"] = "%s%s" % (str(exc), issue_suffix)
            if not rt.summary.get("failed_step"):
                previous = rt.step_records[-1].get("title", "<none>") if rt.step_records else "<none>"
                rt.record_step(
                    "用例断言阶段失败",
                    output="last completed step: %s" % previous,
                    expected="用例完成全部业务步骤和产品行为检测，不抛出异常。",
                    actual=str(exc),
                    result="FAIL",
                    phase="assertion",
                )
                rt.summary["failed_step"] = dict(rt.step_records[-1])
            core_info = rt.detect_new_core()
            print("%-55s FAIL%s" % (case.target, issue_suffix), flush=True)
            print("    reason: %s" % exc, flush=True)
            if core_info is not None:
                core_path, gdb_cmd = core_info
                print("    core: %s" % core_path, flush=True)
                print("    gdb : %s" % gdb_cmd, flush=True)
            failures.append((case.target, rt.summary["reason"]))
        finally:
            rt.stop_fbasecman(best_effort=True, record=False)
            rt.capture_core_log_evidence()
            rt.finish()
            rt.write_summary()
            rt.write_report()
            rt.prune_artifacts()

    print("-------------------------------------------------------------------------------------", flush=True)
    print("Total:", flush=True)
    print("        SUCCESS:%d" % success_count, flush=True)
    print("        FAIL:%d" % len(failures), flush=True)

    if failures:
        lines = ["global_cache failures:"]
        for target_name, reason in failures:
            lines.append("  - %s: %s" % (target_name, reason))
        raise GlobalCacheFailure("\n".join(lines))
