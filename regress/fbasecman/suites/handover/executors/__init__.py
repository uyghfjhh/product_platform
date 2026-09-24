"""Executors registry for handover suite."""

from .routing import (
    execute_mmr_hint_configuration,
    execute_mmr_hint_lifecycle,
    execute_mmr_hint_set_readonly,
    execute_mmr_hint_begin_readonly,
    execute_mmr_port_configuration,
    execute_mmr_port_node_status,
    execute_mmr_port_write,
    execute_mmr_port_read,
    execute_rep_hint_configuration,
    execute_rep_hint_node_status,
    execute_rep_hint_set_readonly,
    execute_rep_hint_begin_readonly,
    execute_rep_port_configuration,
    execute_rep_port_node_status,
    execute_rep_port_write,
    execute_rep_port_read,
)
from .console import (
    execute_console_group_metadata,
    execute_console_server_maintenance,
    execute_console_statistics,
    execute_console_thread_pool_statistics,
    execute_console_reset_statistics,
)
from .ha import execute_ha
from .jdbc import execute_jdbc
from .extensions import (
    execute_heartbeat_interception,
    execute_guc_sync,
    execute_attach_optimization,
    execute_parse_error_single,
    execute_parse_error_multiple,
    execute_global_prepared_statements,
    execute_global_prepared_special_sql,
    execute_global_prepared_eviction,
    execute_global_prepared_bypass_retention,
)


CASE_EXECUTORS = {
    # Chapter 4
    "mmr_hint_configuration": execute_mmr_hint_configuration,
    "mmr_hint_lifecycle": execute_mmr_hint_lifecycle,
    "mmr_hint_set_readonly": execute_mmr_hint_set_readonly,
    "mmr_hint_begin_readonly": execute_mmr_hint_begin_readonly,
    # Chapter 5
    "mmr_port_configuration": execute_mmr_port_configuration,
    "mmr_port_node_status": execute_mmr_port_node_status,
    "mmr_port_write": execute_mmr_port_write,
    "mmr_port_read": execute_mmr_port_read,
    # Chapter 6
    "rep_hint_configuration": execute_rep_hint_configuration,
    "rep_hint_node_status": execute_rep_hint_node_status,
    "rep_hint_set_readonly": execute_rep_hint_set_readonly,
    "rep_hint_begin_readonly": execute_rep_hint_begin_readonly,
    # Chapter 7
    "rep_port_configuration": execute_rep_port_configuration,
    "rep_port_node_status": execute_rep_port_node_status,
    "rep_port_write": execute_rep_port_write,
    "rep_port_read": execute_rep_port_read,
    # Chapter 8
    "ha_write_leader_failure": execute_ha,
    "ha_non_write_leader_failure": execute_ha,
    "ha_all_mmr_failure": execute_ha,
    "ha_replica_failure": execute_ha,
    "ha_non_write_leader_family_failure": execute_ha,
    "ha_rep_primary_failure": execute_ha,
    "ha_rep_promote": execute_ha,
    "ha_rep_replica_failure": execute_ha,
    "ha_rep_all_failure": execute_ha,
    "ha_rep_old_primary_recovery": execute_ha,
    "ha_rep_port_and_sql_parse": execute_ha,
    "ha_monitor_single_failure_retry": execute_ha,
    "ha_monitor_rep_replica_confirm": execute_ha,
    "ha_monitor_rep_primary_promote": execute_ha,
    "ha_monitor_mmr_cascade_blocked": execute_ha,
    "ha_monitor_wal_lag_block": execute_ha,
    "ha_monitor_manual_isolation": execute_ha,
    # Chapter 9
    "jdbc_4227_mmr_hint": execute_jdbc,
    "jdbc_4270_mmr_hint": execute_jdbc,
    "jdbc_4277_mmr_hint": execute_jdbc,
    # Chapter 10
    "console_group_metadata": execute_console_group_metadata,
    "console_server_maintenance": execute_console_server_maintenance,
    "console_statistics": execute_console_statistics,
    "console_thread_pool_statistics": execute_console_thread_pool_statistics,
    "console_reset_statistics": execute_console_reset_statistics,
    # Chapter 11
    "heartbeat_interception": execute_heartbeat_interception,
    "guc_sync": execute_guc_sync,
    "attach_optimization": execute_attach_optimization,
    "parse_error_single": execute_parse_error_single,
    "parse_error_multiple": execute_parse_error_multiple,
    "global_prepared_statements": execute_global_prepared_statements,
    "global_prepared_special_sql": execute_global_prepared_special_sql,
    "global_prepared_eviction": execute_global_prepared_eviction,
    "global_prepared_bypass_retention": execute_global_prepared_bypass_retention,
}


def dispatch_executor(case):
    if case.name in CASE_EXECUTORS:
        return CASE_EXECUTORS[case.name]
    raise KeyError("no executor found for handover case: %s" % case.name)
