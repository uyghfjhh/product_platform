from framework.assertions import command_succeeds, rows_equal
from framework.steps import sql_step


TARGET_NODE = "mmr2"
MMR_NODES = ["mmr:mmr1", "mmr:mmr2", "mmr:mmr3"]


def node_failover_step(node, expected):
    return sql_step(
        "确认 %s 中 %s 的 failover 为 %s" % (node.rsplit(":", 1)[-1], TARGET_NODE, expected),
        "postgres",
        "SELECT node_name::text,failover::text FROM fdd.mmr_node "
        "WHERE node_name = '%s'" % TARGET_NODE,
        "返回 %s|%s" % (TARGET_NODE, expected),
        rows_equal([[TARGET_NODE, expected]]), node=node)


CASE = {
    "id": "mmr.failover_slot.dynamic_failover",
    "name": "多活动态转换故障转移槽标记",
    "document": "多活功能测试文档.md",
    "section": "7.1",
    "group": "failover_slot",
    "fixtures": [
        "cluster",
        {"type": "mmr_global_failover_guard", "node": "mmr:mmr1",
         "nodes": MMR_NODES, "node_name": TARGET_NODE, "expected_state": "true"},
    ],
    "requirements": {
        "plugins": ["fdd_mmr"], "groups": ["mmr"],
        "writable_node": True, "node": "mmr:mmr1",
    },
    "evidence_nodes": MMR_NODES,
    "prerequisites": [
        "三成员多活集群已完成 join，被修改的 mmr2 节点的 failover 在所有成员均为 true。",
        "mmr1 到 mmr2 的 fddoutput 逻辑复制槽存在；fixture 在任何失败路径通过全局 UDF 恢复标记。",
    ],
    "steps": [
        node_failover_step("mmr:mmr1", "true"),
        node_failover_step("mmr:mmr2", "true"),
        node_failover_step("mmr:mmr3", "true"),
        sql_step(
            "确认 mmr2 上指向其他成员的 fddoutput 槽初始为 failover", "postgres",
            "SELECT count(*)::text,bool_and(failover)::text "
            "FROM pg_get_replication_slots() "
            "WHERE plugin = 'fddoutput'",
            "返回 2|true", rows_equal([["2", "true"]]), node="mmr:mmr2"),
        sql_step(
            "按文档全局关闭 mmr2 的 failover 槽标记", "postgres",
            "SELECT fdd.alter_node_failover('mmr2', false, true)",
            "SQL 执行成功", command_succeeds(), node="mmr:mmr1"),
        node_failover_step("mmr:mmr1", "false"),
        node_failover_step("mmr:mmr2", "false"),
        node_failover_step("mmr:mmr3", "false"),
        sql_step(
            "确认 mmr2 上指向其他成员的 fddoutput 槽已取消 failover", "postgres",
            "SELECT count(*)::text,bool_and(NOT failover)::text "
            "FROM pg_get_replication_slots() "
            "WHERE plugin = 'fddoutput'",
            "返回 2|true", rows_equal([["2", "true"]]), node="mmr:mmr2"),
        sql_step(
            "全局恢复 mmr2 的 failover 槽标记", "postgres",
            "SELECT fdd.alter_node_failover('mmr2', true, true)",
            "SQL 执行成功", command_succeeds(), node="mmr:mmr1"),
        node_failover_step("mmr:mmr1", "true"),
        node_failover_step("mmr:mmr2", "true"),
        node_failover_step("mmr:mmr3", "true"),
        sql_step(
            "确认 mmr2 上指向其他成员的 fddoutput 槽已恢复 failover", "postgres",
            "SELECT count(*)::text,bool_and(failover)::text "
            "FROM pg_get_replication_slots() "
            "WHERE plugin = 'fddoutput'",
            "返回 2|true", rows_equal([["2", "true"]]), node="mmr:mmr2"),
    ],
    "teardown": "显式全局恢复 mmr2 的 failover 标记；fixture 在任何失败路径重复调用产品 UDF 恢复。",
}
