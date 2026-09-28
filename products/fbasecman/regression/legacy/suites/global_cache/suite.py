"""Global prepared-statement cache suite entry and remaining scenarios."""

import os
import re
import shutil
from contextlib import contextmanager
from copy import copy

from platform_regress.evidence.assertions import stats_delta as _stats_delta
from platform_regress.execution.phased_process import PhaseAction, observe_phases
from platform_regress.evidence.log_checks import find_forbidden_log_patterns
from products.fbasecman.console import parse_pipe_rows
from platform_regress.reporting import render_psql_table_from_pipe_text
from platform_regress.configuration.reload import (
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
from suites.global_cache.dispatch import (
    COMPOSITE_PHASE_NAMES,
    SPECIAL_CASE_EXECUTORS,
    STARTED_CASE_EXECUTORS,
    _case_phase,
    _collect_phase_checks,
    _execute_case,
    _execute_started_case,
)


def _run_case(root, env, context, case):
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
    finally:
        rt.stop_fbasecman(best_effort=True, record=False)
        core_info = rt.detect_new_core()
        if core_info is not None:
            core_path, gdb_cmd = core_info
            reason = "fbasecman generated core dump during execution or shutdown: %s" % core_path
            previous_reason = rt.summary.get("reason")
            rt.summary["status"] = "FAIL"
            rt.summary["reason"] = (
                "%s; %s" % (previous_reason, reason) if previous_reason else reason
            )
            rt.record_step(
                "产品进程崩溃检测",
                expected="用例执行及进程停止期间不产生 core dump。",
                actual=core_path,
                result="FAIL",
                phase="cleanup",
            )
            rt.summary["failed_step"] = dict(rt.step_records[-1])
            print("    core: %s" % core_path, flush=True)
            print("    gdb : %s" % gdb_cmd, flush=True)
        rt.capture_core_log_evidence()
        rt.finish()
        rt.write_summary()
        rt.write_report()
        rt.prune_artifacts()
    passed = rt.summary["status"] == "PASS"
    if passed:
        print("%-55s SUCCESS" % case.target, flush=True)
    else:
        print("%-55s FAIL" % case.target, flush=True)
        print("    reason: %s" % rt.summary.get("reason", "unknown"), flush=True)
    return passed, rt.summary.get("reason", "unknown")


def run_case(root, case):
    """Platform engine entry: execute one manifest case, return pass/fail."""
    env, context = _load_env(root)
    passed, _ = _run_case(root, env, context, case)
    return passed


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
        passed, reason = _run_case(root, env, context, case)
        if passed:
            success_count += 1
        else:
            failures.append((case.target, reason))

    print("-------------------------------------------------------------------------------------", flush=True)
    print("Total:", flush=True)
    print("        SUCCESS:%d" % success_count, flush=True)
    print("        FAIL:%d" % len(failures), flush=True)

    if failures:
        lines = ["global_cache failures:"]
        for target_name, reason in failures:
            lines.append("  - %s: %s" % (target_name, reason))
        raise GlobalCacheFailure("\n".join(lines))
    return True
