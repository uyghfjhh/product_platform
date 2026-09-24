"""HA console command suite entry point."""

import time
from pathlib import Path

from framework.configuration import load_regression_config
from .manifest import HA_COMMAND_CASES, case_items, find_case, validate_manifest
from .runtime import HaCommandFailure, HaCommandRuntime
from .helpers import *
from .executors import *


EXECUTORS = {
    "set_node_write_idempotent": _run_set_node_write_idempotent,
    "set_node_promoted_idempotent": _run_set_node_promoted_idempotent,
    "set_cluster_active_idempotent": _run_set_cluster_active_idempotent,
    "set_cluster_write_promoted_roundtrip": _run_set_cluster_write_promoted_roundtrip,
    "set_cluster_parted_active_roundtrip": _run_set_cluster_parted_active_roundtrip,
    "set_node_write_switch_and_restore": _run_set_node_write_switch_and_restore,
    "set_node_write_all_related_groups": _run_set_node_write_all_related_groups,
    "refresh_cluster": _run_refresh_cluster,
    "set_node_invalid_datasource": _run_set_node_invalid_datasource,
    "set_node_weight_switch_and_restore": _run_set_node_weight_switch_and_restore,
    "set_node_weight_idempotent": _run_set_node_weight_idempotent,
    "set_node_write_in_groups_roundtrip": _run_set_node_write_in_groups_roundtrip,
    "set_node_promoted_in_groups_missing_field": _run_set_node_promoted_in_groups_missing_field,
    "set_node_write_in_groups_invalid_group": _run_set_node_write_in_groups_invalid_group,
    "duplicate_and_conflicting_weights": _run_duplicate_and_conflicting_weights,
    "duplicate_and_mixed_status_targets": _run_duplicate_and_mixed_status_targets,
    "name_endpoint_status_deduplication": _run_name_endpoint_status_deduplication,
    "batch_mixed_no_change_and_change": _run_batch_mixed_no_change_and_change,
    "set_node_write_in_groups_duplicate_group": _run_set_node_write_in_groups_duplicate_group,
    "set_node_write_in_groups_cardinality_errors": _run_set_node_write_in_groups_cardinality_errors,
    "batch_status_invalid_target_atomicity": _run_batch_status_invalid_target_atomicity,
    "set_node_weight_invalid_values": _run_set_node_weight_invalid_values,
    "weight_sum_overflow_rejected": _run_weight_sum_overflow_rejected,
    "locked_invalid_numeric_token": _run_locked_invalid_numeric_token,
    "locked_disk_object_resolution": _run_locked_disk_object_resolution,
    "set_node_weight_zero_roundtrip": _run_set_node_weight_zero_roundtrip,
    "set_cluster_invalid_commands": _run_set_cluster_invalid_commands,
    "console_set_validation_toggle": _run_console_set_validation_toggle,
    "refresh_cluster_syntax_errors": _run_refresh_cluster_syntax_errors,
    "refresh_cluster_probe_edges": _run_refresh_cluster_probe_edges,
    "set_node_role_rejects_parted_target": _run_set_node_role_rejects_parted_target,
    "set_node_role_rejects_unrelated_group": _run_set_node_role_rejects_unrelated_group,
    "weight_format_preservation": _run_weight_format_preservation,
    "include_rejected_after_start": _run_include_rejected_after_start,
    "duplicate_object_rejected_after_start": _run_duplicate_object_rejected_after_start,
    "application_name_persistence": _run_application_name_persistence,
    "group_defaults_persistence": _run_group_defaults_persistence,
    "single_read_only_persistence": _run_single_read_only_persistence,
    "bulk_30_group_write_roundtrip": _run_bulk_30_group_write_roundtrip,
    "default_group_expansion_34": _run_default_group_expansion_34,
    "bulk_30_groups_invalid_target": _run_bulk_30_groups_invalid_target,
    "bulk_30_groups_non_mmr": _run_bulk_30_groups_non_mmr,
    "set_cluster_30_datasource_roundtrip": _run_set_cluster_30_datasource_roundtrip,
    "bulk_30_datasource_weight_roundtrip": _run_bulk_30_datasource_weight_roundtrip,
    "candidate_validation_rejected": _run_candidate_validation_rejected,
    "reload_failure_rollback": _run_reload_failure_rollback,
    "file_metadata_preservation": _run_file_metadata_preservation,
    "backup_symlink_rejected": _run_backup_symlink_rejected,
    "stable_lock_contention": _run_stable_lock_contention,
    "stable_lock_symlink_rejected": _run_stable_lock_symlink_rejected,
    "backup_directory_permissions": _run_backup_directory_permissions,
    "stable_lock_permissions": _run_stable_lock_permissions,
    "stable_lock_directory_rejected": _run_stable_lock_directory_rejected,
    "backup_path_regular_file_rejected": _run_backup_path_regular_file_rejected,
    "config_backup_dir": _run_config_backup_dir,
    "readonly_config_directory": _run_readonly_config_directory,
    "readonly_config_file": _run_readonly_config_file,
    "rename_failure_protection": _run_rename_failure_protection,
    "reload_restore_failure": _run_reload_restore_failure,
    "external_edit_conflict": _run_external_edit_conflict,
    "write_cluster_format_preservation": _run_write_cluster_format_preservation,
    "status_format_preservation": _run_status_format_preservation,
    "crlf_format_preservation": _run_crlf_format_preservation,
    "eof_without_newline_preservation": _run_eof_without_newline_preservation,
    "single_line_block_preservation": _run_single_line_block_preservation,
    "hash_inside_string_preservation": _run_hash_inside_string_preservation,
    "set_node_parted_active_roundtrip": _run_set_node_parted_active_roundtrip,
    "set_node_write_non_mmr_group": _run_set_node_write_non_mmr_group,
    "four_group_modes_route_visibility": _run_four_group_modes_route_visibility,
    "mmr_hint_route": _run_mmr_hint_route,
    "replication_route": _run_replication_route,
    "mmr_hint_read_route": _run_mmr_hint_read_route,
    "rep_hint_write_route": _run_rep_hint_write_route,
    "mmr_port_write_route": _run_mmr_port_write_route,
    "rep_port_write_route": _run_rep_port_write_route,
    "mmr_port_read_route": _run_mmr_port_read_route,
    "rep_port_read_route": _run_rep_port_read_route,
    "mmr_sql_parse_read_write_transactions": _run_mmr_sql_parse_read_write_transactions,
    "rep_sql_parse_read_write_transactions": _run_rep_sql_parse_read_write_transactions,
    "sql_parse_extended_protocol": _run_sql_parse_extended_protocol,
    "jdbc_console_ha_commands": _run_jdbc_console_ha_commands,
    "ha_command_role_change_route_matrix": _run_mixed_topology_pool_mode_batch_write,
    "balance_route": _run_balance_route,
    "balance_read_only_route": _run_balance_read_only_route,
    "single_route": _run_single_route,
    "set_node_promoted_write_cluster_conflict": _run_set_node_promoted_write_cluster_conflict,
    "missing_promoted_set_write": _run_missing_promoted_set_write,
    "missing_promoted_set_promoted": _run_missing_promoted_set_promoted,
    "batch_weight_atomicity": _run_batch_weight_atomicity,
    "comprehensive_all_groups_and_commands": _run_comprehensive_all_groups_and_commands,
}


def show():
    validate_manifest()
    lines = ["ha_commands - 高可用控制台命令及持久化测试"]
    for case in HA_COMMAND_CASES:
        lines.append("  - %-42s 来源=%s %s" % (
            case.target, ",".join(case.source_sections), case.summary,
        ))
    lines.append("")
    lines.append("Default gate: %d / %d" % (len(case_items()), len(HA_COMMAND_CASES)))
    return "\n".join(lines)


def run_case(root, case):
    started = time.monotonic()
    rt = None
    try:
        rt = HaCommandRuntime(root, case)
        with rt:
            EXECUTORS[case.executor](rt)
        rt.finish("PASS", "命令输入输出及用例声明的配置、日志和运行态证据均符合预期。")
        print("%-58s SUCCESS %8.3fs" % (case.target, time.monotonic() - started))
        return True
    except Exception as exc:
        if rt is not None:
            try:
                rt.finish("FAIL", str(exc))
            except Exception:
                rt.stop()
        else:
            # Initialization errors must leave an artifact at the standard
            # case location, otherwise a failed run is impossible to inspect.
            run_root = (load_regression_config(Path(root)).output_dir / "runs" /
                        "ha_commands" / case.name)
            run_root.mkdir(parents=True, exist_ok=True)
            (run_root / "report.txt").write_text(
                "Test: %s\nStatus: FAIL\nSummary: %s\nFailure: %s\n"
                "Steps: <runtime initialization failed before steps could run>\n" %
                (case.target, case.summary, exc), encoding="utf-8")
        print("%-58s FAIL    %8.3fs" % (case.target, time.monotonic() - started))
        return False


def run(root, target=None):
    validate_manifest()
    selected = [find_case(target)] if target else case_items()
    failures = sum(0 if run_case(root, case) else 1 for case in selected)
    print("Total: SUCCESS:%d FAIL:%d" % (len(selected) - failures, failures))
    return failures == 0
