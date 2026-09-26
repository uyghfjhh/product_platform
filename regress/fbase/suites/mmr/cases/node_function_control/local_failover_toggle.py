from framework.assertions import command_succeeds, rows_equal
from framework.steps import sql_step


CASE = {
    "id": "mmr.node_function_control.local_failover_toggle",
    "name": "多活节点 failover 本地修改与槽联动",
    "document": "多活功能测试文档.md",
    "section": "9.5 测试二",
    "group": "node_function_control",
    "fixtures": [
        "cluster",
        {"type": "mmr_node_failover_guard", "node": "mmr:mmr1",
         "node_name": "mmr2", "expected_state": "true"},
    ],
    "requirements": {
        "plugins": ["fdd_mmr"], "groups": ["mmr"],
        "writable_node": True, "node": "mmr:mmr1",
    },
    "evidence_nodes": ["mmr:mmr1", "mmr:mmr2"],
    "prerequisites": [
        "mmr1、mmr2 均为 ACTIVE，mmr1 记录的 mmr2 failover 初始值为 true。",
        "mmr2 上存在指向 mmr1 的 fddoutput 槽；fixture 会在失败路径用产品 UDF 恢复元数据和槽标记。",
    ],
    "steps": [
        sql_step(
            "确认 mmr1 本地记录的 mmr2 failover 初始为 true", "postgres",
            "SELECT node_name::text,failover::text FROM fdd.mmr_node "
            "WHERE node_name = 'mmr2'",
            "返回 mmr2|true", rows_equal([["mmr2", "true"]]), node="mmr:mmr1"),
        sql_step(
            "确认 mmr2 中指向 mmr1 的 fddoutput 槽初始为 failover", "postgres",
            "SELECT count(*)::text,bool_and(failover)::text "
            "FROM pg_get_replication_slots() "
            "WHERE plugin = 'fddoutput' AND slot_name LIKE '%mmr1'",
            "返回 1|true", rows_equal([["1", "true"]]), node="mmr:mmr2"),
        sql_step(
            "按文档仅在 mmr1 修改 mmr2 的 failover 为 false", "postgres",
            "SELECT fdd.alter_node_info('failover', 'mmr2', 'false', false)",
            "SQL 执行成功", command_succeeds(), node="mmr:mmr1"),
        sql_step(
            "确认 mmr1 本地元数据中的 mmr2 failover 已关闭", "postgres",
            "SELECT node_name::text,failover::text FROM fdd.mmr_node "
            "WHERE node_name = 'mmr2'",
            "返回 mmr2|false", rows_equal([["mmr2", "false"]]), node="mmr:mmr1"),
        sql_step(
            "确认 mmr2 中指向 mmr1 的 fddoutput 槽已关闭 failover", "postgres",
            "SELECT count(*)::text,bool_and(NOT failover)::text "
            "FROM pg_get_replication_slots() "
            "WHERE plugin = 'fddoutput' AND slot_name LIKE '%mmr1'",
            "返回 1|true", rows_equal([["1", "true"]]), node="mmr:mmr2"),
        sql_step(
            "恢复 mmr1 本地 mmr2 的 failover", "postgres",
            "SELECT fdd.alter_node_info('failover', 'mmr2', 'true', false)",
            "SQL 执行成功", command_succeeds(), node="mmr:mmr1"),
        sql_step(
            "确认 mmr1 本地元数据中的 mmr2 failover 已恢复", "postgres",
            "SELECT node_name::text,failover::text FROM fdd.mmr_node "
            "WHERE node_name = 'mmr2'",
            "返回 mmr2|true", rows_equal([["mmr2", "true"]]), node="mmr:mmr1"),
        sql_step(
            "确认 mmr2 中指向 mmr1 的 fddoutput 槽已恢复 failover", "postgres",
            "SELECT count(*)::text,bool_and(failover)::text "
            "FROM pg_get_replication_slots() "
            "WHERE plugin = 'fddoutput' AND slot_name LIKE '%mmr1'",
            "返回 1|true", rows_equal([["1", "true"]]), node="mmr:mmr2"),
    ],
    "teardown": "用例显式恢复本地 failover；fixture 在任意失败路径调用产品 UDF 恢复 mmr1 元数据和 mmr2 对应复制槽。",
}
