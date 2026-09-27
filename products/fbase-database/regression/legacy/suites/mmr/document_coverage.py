"""Transfer-document mapping for the MMR suite.

Every registered case is mapped here.  Empty mappings are an explicit backlog
item; the MMR suite is deliberately not marked coverage_complete until every
executable document scenario is implemented or given an explicit exemption.
"""


DOCUMENT_TEST_POINTS = {
    "多活功能测试文档.md": {
        "5.1.1": ["mmr.cluster_verification.basic"],
        "5.1.3": ["mmr.cluster_verification.node_state"],
        "5.2 测试一": ["mmr.cluster_verification.check_node_conf_table_exclusion"],
        "5.2 测试二": ["mmr.cluster_verification.check_node_conf_failover_exclusion"],
        "7.1": ["mmr.failover_slot.dynamic_failover"],
        "9.1": ["mmr.remote_sql.command_and_all_nodes"],
        "9.5 测试一": ["mmr.node_function_control.streaming_toggle"],
        "9.5 测试二": ["mmr.node_function_control.local_failover_toggle"],
        "9.5 测试三": ["mmr.node_function_control.two_phase_change_unsupported"],
        "9.6.1.1": ["mmr.global_sequence.snowflake_nextval"],
        "9.6.1.2": ["mmr.global_sequence.snowflake_conversion"],
        "9.6.2.1 测试一": ["mmr.global_sequence.increment_offset_conversion"],
        "9.6.2.2": ["mmr.global_sequence.increment_offset_deletion"],
        "9.6.2.3 测试一": ["mmr.global_sequence.increment_offset_settings"],
        "9.6.2.4 测试一": ["mmr.global_sequence.increment_offset_refresh"],
    },
}


# Full functional inventory extracted from 多活功能测试文档.md.  Existing
# mappings above are intentionally not repeated here.  Scenario-level points
# keep multi-step transfer flows visible until a dedicated case is added.
DOCUMENT_TEST_POINTS["多活功能测试文档.md"].update({
    "1.1": ["mmr.installation.runtime_prerequisites"],
    "1.2": ["mmr.installation.runtime_prerequisites"],
    "2.1": ["mmr.node_management.create_node"],
    "2.2 测试一": ["mmr.node_management.create_group"],
    "2.2 测试二": ["mmr.node_management.create_group"],
    "2.2 测试三": ["mmr.node_management.create_group"],
    "2.3.1": ["mmr.node_management.join_group"],
    "2.3.2.1 测试一": ["mmr.node_management.online_join_all"], "2.3.2.1 测试二": ["mmr.node_management.online_join_all_retry"],
    "2.3.2.1 测试三": ["mmr.node_management.online_join_data_only"], "2.3.2.1 测试四": ["mmr.node_management.online_join_data_retry"],
    "2.3.2.2 测试一": ["mmr.node_management.multi_database_active_join"], "2.3.2.2 测试二": ["mmr.node_management.multi_database_three_node_join"],
    "2.4 force=true 活跃节点": ["mmr.node_management.part_node_online"],
    "2.4 force=false 活跃节点": ["mmr.node_management.part_node_online"],
    "2.4 force=true 停机节点": ["mmr.node_management.part_node_offline"],
    "2.4 force=false 停机节点": ["mmr.node_management.part_node_offline"],
    "2.5": ["mmr.node_management.drop_node"],
    "2.6": ["mmr.node_management.wait_for_join_completion"],
    "3.1": ["mmr.replication_set.global_default_document_requirement"],
    "3.2": ["mmr.replication_set.private_lifecycle"],
    "3.3": ["mmr.replication_set.private_lifecycle"], "3.4": ["mmr.replication_set.subscription_sets"],
    "3.5 测试一": ["mmr.replication_set.auto_table_binding"],
    "3.5 测试二": ["mmr.replication_set.auto_table_binding"],
    "3.5 测试三": ["mmr.replication_set.table_async_lifecycle"],
    "3.5 测试四": ["mmr.replication_set.schema_table_binding"],
    "3.6 测试一": ["mmr.replication_set.table_async_lifecycle"],
    "3.6 测试二": ["mmr.replication_set.synchronous_removal"],
    "3.7": ["mmr.replication_set.synchronous_removal"],
    "4.1.1": ["mmr.conflict.update_missing"], "4.1.2": ["mmr.conflict.delete_missing"], "4.1.3": ["mmr.conflict.insert_exists"], "4.1.4": ["mmr.conflict.update_pkey_exists"],
    "4.1.5": ["mmr.conflict.asymmetric_columns"], "4.1.6": ["mmr.conflict.asymmetric_columns"], "4.1.7": ["mmr.conflict.target_table_missing"], "4.1.8": ["mmr.conflict.multiple_unique_conflicts"],
    "4.1.9": ["mmr.conflict.update_recently_deleted"], "4.1.10": ["mmr.conflict.update_origin_change"], "4.1.11": ["mmr.conflict.delete_recently_updated"],
    "4.2 本地配置": ["mmr.conflict.resolver_configuration"], "4.2 指定节点配置": ["mmr.conflict.resolver_configuration"], "4.2 全节点配置": ["mmr.conflict.resolver_configuration"],
    "4.3.1.1": ["mmr.conflict.update_insert_order"], "4.3.1.2": ["mmr.conflict.insert_update_order"], "4.3.1.3": ["mmr.conflict.simultaneous_insert_update"],
    "4.3.2.1": ["mmr.conflict.update_update_primary_key"], "4.3.2.2": ["mmr.conflict.update_update_unique_identity"],
    "4.3.3.1": ["mmr.conflict.update_update_identity_full_no_pk"], "4.3.3.2": ["mmr.conflict.update_update_identity_full_pk"],
    "4.3.4.1": ["mmr.conflict.update_delete"], "4.3.4.2": ["mmr.conflict.update_delete"], "4.4": ["mmr.conflict.log_configuration"],
    "5.1.2": [
        "mmr.cluster_verification.connection_failure_priority",
        "mmr.cluster_verification.same_priority_errors",
        "mmr.cluster_verification.subscription_existence_priority",
    ],
    "5.1.4": ["mmr.cluster_verification.time_difference"],
    "6": ["mmr.background.maintenance_lifecycle"], "7.2": ["mmr.failover_slot.physical_failover_interface"], "7.3": ["mmr.failover_slot.physical_failover_interface"],
    "8.1": ["mmr.subscription_control.enable_disable"],
    "8.2": ["mmr.subscription_control.enable_disable"],
    "9.2": ["mmr.forwarding.ordinary_logical"],
    "9.3": ["mmr.two_phase.transaction_commit"],
    "9.4.1": ["mmr.streaming.default_publication_preparation"],
    "9.4.1.1": ["mmr.streaming.buffered_native_parallel"],
    "9.4.1.2": ["mmr.streaming.immediate_native_parallel"], "9.4.2": [],
    "9.6.2.1 测试二": ["mmr.global_sequence.increment_offset_schema_names"],
    "9.6.2.3 测试二": ["mmr.global_sequence.increment_offset_node_id"],
    "9.6.2.3 测试三": ["mmr.global_sequence.increment_offset_settings"],
    "9.6.2.4 测试二": ["mmr.global_sequence.increment_offset_refresh"],
    "9.6.2.5": ["mmr.global_sequence.invalid_metadata_cleanup"],
    "9.6.3.1": ["mmr.global_sequence.metadata_consistency"],
    "9.6.3.2": ["mmr.global_sequence.join_behavior"],
    "9.6.3.3": ["mmr.global_sequence.nonforce_part_metadata"],
    "9.7": ["mmr.default_publication.schema_filtering"],
    "9.8.1": ["mmr.node_management.physical_to_logical_join"], "9.8.2": ["mmr.node_management.physical_to_mmr_join"], "9.8.3": ["mmr.node_management.multi_database_physical_to_mmr_join"],
    "10": ["mmr.installation.uninstall_guard"], "11.1": ["mmr.installation.license_lifecycle"], "11.2": ["mmr.installation.license_lifecycle"], "11.3.1": ["mmr.installation.license_lifecycle"], "11.3.2": ["mmr.installation.license_lifecycle"],
    "12": [],
})

DOCUMENT_TEST_POINTS["多活streaming冲突处理测试文档.md"] = {
    "1": ["mmr.streaming_conflict.configuration_matrix"],
    "2.1.1": ["mmr.streaming_conflict.delete_missing_skip"],
    "2.1.2": ["mmr.streaming_conflict.delete_missing_error"],
    "2.1.3": ["mmr.streaming_conflict.delete_missing_skip_transaction"],
    "2.2.1": ["mmr.streaming_conflict.update_missing_insert_or_skip"],
    "2.2.2": ["mmr.streaming_conflict.update_missing_error"],
    "2.2.3": ["mmr.streaming_conflict.update_missing_skip"],
    "2.2.4": ["mmr.streaming_conflict.update_missing_insert_or_error"],
    "2.2.5": ["mmr.streaming_conflict.update_missing_skip_transaction"],
    "2.3.1": ["mmr.streaming_conflict.insert_exists_update_if_newer",
               "mmr.streaming_conflict.insert_exists_savepoint_rollback"],
    "2.3.2": ["mmr.streaming_conflict.insert_exists_error"],
    "2.3.3": ["mmr.streaming_conflict.insert_exists_skip",
               "mmr.streaming_conflict.insert_exists_multi_transaction"],
    "2.3.4": ["mmr.streaming_conflict.insert_exists_update"],
    "2.3.5": ["mmr.streaming_conflict.insert_exists_skip_transaction"],
    "2.4.1": ["mmr.streaming_conflict.update_pkey_exists_update_if_newer"],
    "2.4.2": ["mmr.streaming_conflict.update_pkey_exists_error"],
    "2.4.3": ["mmr.streaming_conflict.update_pkey_exists_skip"],
    "2.4.4": ["mmr.streaming_conflict.update_pkey_exists_update"],
    "2.4.5": ["mmr.streaming_conflict.update_pkey_exists_skip_transaction"],
    "2.5.1": ["mmr.streaming_conflict.update_recently_deleted_skip"],
    "2.5.2": ["mmr.streaming_conflict.update_recently_deleted_error"],
    "2.5.3": ["mmr.streaming_conflict.update_recently_deleted_insert_or_skip"],
    "2.5.4": ["mmr.streaming_conflict.update_recently_deleted_insert_or_error"],
    "2.5.5": ["mmr.streaming_conflict.update_recently_deleted_skip_transaction"],
    "2.6.1": ["mmr.streaming_conflict.delete_recently_updated_skip"],
    "2.6.2": ["mmr.streaming_conflict.delete_recently_updated_error"],
    "2.6.3": ["mmr.streaming_conflict.delete_recently_updated_update"],
    "2.7.1": ["mmr.streaming_conflict.target_column_missing_ignore_if_null"],
    "2.7.2": ["mmr.streaming_conflict.target_column_missing_skip"],
    "2.7.3": ["mmr.streaming_conflict.target_column_missing_error"],
    "2.7.4": ["mmr.streaming_conflict.target_column_missing_ignore"],
    "2.8.1": ["mmr.streaming_conflict.source_column_missing_use_default_value"],
    "2.8.2": ["mmr.streaming_conflict.source_column_missing_error"],
    "2.8.3": ["mmr.streaming_conflict.source_column_missing_skip"],
    "2.9.1": ["mmr.streaming_conflict.target_table_missing_skip_if_recently_dropped"],
    "2.9.2": ["mmr.streaming_conflict.target_table_missing_error"],
    "2.9.3": ["mmr.streaming_conflict.target_table_missing_skip"],
    "2.10.1": ["mmr.streaming_conflict.multiple_unique_conflicts_error"],
    "2.10.2": ["mmr.streaming_conflict.multiple_unique_conflicts_skip"],
    "2.11.1": ["mmr.streaming_conflict.update_origin_change_update_if_newer"],
    "2.11.2": ["mmr.streaming_conflict.update_origin_change_error"],
    "2.11.3": ["mmr.streaming_conflict.update_origin_change_skip"],
    "2.11.4": ["mmr.streaming_conflict.update_origin_change_update"],
    "2.11.5": ["mmr.streaming_conflict.update_origin_change_skip_transaction"],
    "3.1.1": ["mmr.streaming_conflict.two_phase_delete_missing_skip"],
    "3.1.2": ["mmr.streaming_conflict.two_phase_delete_missing_error"],
    "3.1.3": ["mmr.streaming_conflict.two_phase_delete_missing_skip_transaction"],
    "3.2.1": ["mmr.streaming_conflict.two_phase_update_missing_insert_or_skip"],
    "3.2.2": ["mmr.streaming_conflict.two_phase_update_missing_error"],
    "3.2.3": ["mmr.streaming_conflict.two_phase_update_missing_skip"],
    "3.2.4": ["mmr.streaming_conflict.two_phase_update_missing_insert_or_error"],
    "3.2.5": ["mmr.streaming_conflict.two_phase_update_missing_skip_transaction"],
    "3.3.1": ["mmr.streaming_conflict.two_phase_insert_exists_update_if_newer"],
    "3.3.2": ["mmr.streaming_conflict.two_phase_insert_exists_error"],
    "3.3.3": ["mmr.streaming_conflict.two_phase_insert_exists_skip",
               "mmr.streaming_conflict.two_phase_insert_exists_mixed_transactions"],
    "3.3.4": ["mmr.streaming_conflict.two_phase_insert_exists_update"],
    "3.3.5": ["mmr.streaming_conflict.two_phase_insert_exists_skip_transaction"],
    "3.4.1": ["mmr.streaming_conflict.two_phase_update_pkey_exists_update_if_newer"],
    "3.4.2": ["mmr.streaming_conflict.two_phase_update_pkey_exists_error"],
    "3.4.3": ["mmr.streaming_conflict.two_phase_update_pkey_exists_skip"],
    "3.4.4": ["mmr.streaming_conflict.two_phase_update_pkey_exists_update"],
    "3.4.5": ["mmr.streaming_conflict.two_phase_update_pkey_exists_skip_transaction"],
    "3.5.1": ["mmr.streaming_conflict.two_phase_update_recently_deleted_skip"],
    "3.5.2": ["mmr.streaming_conflict.two_phase_update_recently_deleted_error"],
    "3.5.3": ["mmr.streaming_conflict.two_phase_update_recently_deleted_insert_or_skip"],
    "3.5.4": ["mmr.streaming_conflict.two_phase_update_recently_deleted_insert_or_error"],
    "3.5.5": ["mmr.streaming_conflict.two_phase_update_recently_deleted_skip_transaction"],
    "3.6.1": ["mmr.streaming_conflict.two_phase_delete_recently_updated_skip"],
    "3.6.2": ["mmr.streaming_conflict.two_phase_delete_recently_updated_error"],
    "3.6.3": ["mmr.streaming_conflict.two_phase_delete_recently_updated_update"],
    "3.7.1": ["mmr.streaming_conflict.two_phase_target_column_missing_ignore_if_null"],
    "3.7.2": ["mmr.streaming_conflict.two_phase_target_column_missing_skip"],
    "3.7.3": ["mmr.streaming_conflict.two_phase_target_column_missing_error"],
    "3.7.4": ["mmr.streaming_conflict.two_phase_target_column_missing_ignore"],
    "3.8.1": ["mmr.streaming_conflict.two_phase_source_column_missing_use_default_value"],
    "3.8.2": ["mmr.streaming_conflict.two_phase_source_column_missing_error"],
    "3.8.3": ["mmr.streaming_conflict.two_phase_source_column_missing_skip"],
    "3.9.1": ["mmr.streaming_conflict.two_phase_target_table_missing_skip_if_recently_dropped"],
    "3.9.2": ["mmr.streaming_conflict.two_phase_target_table_missing_error"],
    "3.9.3": ["mmr.streaming_conflict.two_phase_target_table_missing_skip"],
    "3.10.1": ["mmr.streaming_conflict.two_phase_multiple_unique_conflicts_error"],
    "3.10.2": ["mmr.streaming_conflict.two_phase_multiple_unique_conflicts_skip"],
    "3.11.1": ["mmr.streaming_conflict.two_phase_update_origin_change_update_if_newer"],
    "3.11.2": ["mmr.streaming_conflict.two_phase_update_origin_change_error"],
    "3.11.3": ["mmr.streaming_conflict.two_phase_update_origin_change_skip"],
    "3.11.4": ["mmr.streaming_conflict.two_phase_update_origin_change_update"],
    "3.11.5": ["mmr.streaming_conflict.two_phase_update_origin_change_skip_transaction"],
}


DOCUMENT_TEST_POINT_EXEMPTIONS = {
    "多活功能测试文档.md": {
        "9.4.2": (
            "本节仅指向《多活streaming冲突处理测试文档.md》；其 2.x/3.x "
            "可执行冲突点均已在该文档中逐项映射，不重复创建功能文档 case。"
        ),
        "12": "本节为限制和建议说明，不含可执行操作、输入或可判定期望结果。",
    },
}


# Independent regression scenarios in mmr-autotest.  This is not a third
# transfer document: it records the source-suite cross-check requested for
# MMR so scenario-level regressions do not disappear behind the document's
# strategy matrix.
MMR_AUTOTEST_SCENARIOS = {
    "test_2pc_mixed_single.py": [
        "mmr.streaming_conflict.two_phase_insert_exists_mixed_transactions",
    ],
    "test_rollback_single.py": [
        "mmr.streaming_conflict.insert_exists_multi_transaction",
    ],
    "test_delete_missing.py": [
        "mmr.streaming_conflict.delete_missing_skip",
        "mmr.streaming_conflict.two_phase_delete_missing_skip",
    ],
    "test_delete_recently_update.py": [
        "mmr.streaming_conflict.delete_recently_updated_skip",
        "mmr.streaming_conflict.two_phase_delete_recently_updated_skip",
    ],
    "test_insert_exists.py": [
        "mmr.streaming_conflict.insert_exists_update_if_newer",
        "mmr.streaming_conflict.two_phase_insert_exists_update_if_newer",
    ],
    "test_insert_exists_multi_transaction.py": [
        "mmr.streaming_conflict.insert_exists_multi_transaction",
    ],
    "test_insert_exists_savepoint_rollback.py": [
        "mmr.streaming_conflict.insert_exists_savepoint_rollback",
    ],
    "test_insert_exists_2pc_mixed.py": [
        "mmr.streaming_conflict.two_phase_insert_exists_mixed_transactions",
    ],
    "test_multiple_unique_conflicts.py": [
        "mmr.streaming_conflict.multiple_unique_conflicts_error",
        "mmr.streaming_conflict.two_phase_multiple_unique_conflicts_error",
    ],
    "test_source_column_missing.py": [
        "mmr.streaming_conflict.source_column_missing_use_default_value",
        "mmr.streaming_conflict.two_phase_source_column_missing_use_default_value",
    ],
    "test_target_column_missing.py": [
        "mmr.streaming_conflict.target_column_missing_ignore_if_null",
        "mmr.streaming_conflict.two_phase_target_column_missing_ignore_if_null",
    ],
    "test_target_table_missing.py": [
        "mmr.streaming_conflict.target_table_missing_skip_if_recently_dropped",
        "mmr.streaming_conflict.two_phase_target_table_missing_skip_if_recently_dropped",
    ],
    "test_update_missing.py": [
        "mmr.streaming_conflict.update_missing_insert_or_skip",
        "mmr.streaming_conflict.two_phase_update_missing_insert_or_skip",
    ],
    "test_update_origin_change.py": [
        "mmr.streaming_conflict.update_origin_change_update_if_newer",
        "mmr.streaming_conflict.two_phase_update_origin_change_update_if_newer",
    ],
    "test_update_pkey_exists.py": [
        "mmr.streaming_conflict.update_pkey_exists_update_if_newer",
        "mmr.streaming_conflict.two_phase_update_pkey_exists_update_if_newer",
    ],
    "test_update_recently_deleted.py": [
        "mmr.streaming_conflict.update_recently_deleted_skip",
        "mmr.streaming_conflict.two_phase_update_recently_deleted_skip",
    ],
    "fdd_mmr_join_jions/tests/test_join_base.py": [
        "mmr.node_management.physical_to_logical_join",
    ],
    "fdd_mmr_join_jions/tests/test_join_single_db.py": [
        "mmr.node_management.physical_to_mmr_join",
    ],
    "fdd_mmr_join_jions/tests/test_join_multi_db.py": [
        "mmr.node_management.multi_database_physical_to_mmr_join",
    ],
}


MMR_AUTOTEST_EXEMPTIONS = {
    "test_multi_node_demo.py": (
        "该文件验证 mmr-autotest 自身的连接、清理和兼容性辅助函数；"
        "不是产品测试场景，且它的三节点数据同步已由转测文档 2.3.2.2/"
        "9.8.3 的 MMR 用例覆盖。"
    ),
}


def pending_test_points():
    return [
        (document, point)
        for document, points in DOCUMENT_TEST_POINTS.items()
        for point, case_ids in points.items()
        if not case_ids and not DOCUMENT_TEST_POINT_EXEMPTIONS.get(document, {}).get(point)
    ]


def implemented_case_ids():
    return {
        case_id
        for points in DOCUMENT_TEST_POINTS.values()
        for case_ids in points.values()
        for case_id in case_ids
    }


def exempted_test_points():
    return {
        (document, point): reason
        for document, points in DOCUMENT_TEST_POINT_EXEMPTIONS.items()
        for point, reason in points.items()
    }
