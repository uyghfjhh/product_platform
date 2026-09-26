from framework.assertions import command_succeeds, rows_equal
from framework.steps import sql_step


MMR_NODES = ["mmr:mmr1", "mmr:mmr2", "mmr:mmr3"]
TARGET_NODE = "mmr2"


def node_streaming_step(node, expected):
    return sql_step(
        "确认 %s 中 %s 的 streaming 为 %s" %
        (node.rsplit(":", 1)[-1], TARGET_NODE, expected),
        "postgres",
        "SELECT node_name::text,streaming::text FROM fdd.mmr_node "
        "WHERE node_name = '%s'" % TARGET_NODE,
        "返回 %s|%s" % (TARGET_NODE, expected),
        rows_equal([[TARGET_NODE, expected]]), node=node)


def subscriptions_step(node, expected):
    return sql_step(
        "确认 %s 的全部多活订阅 substream 为 %s" %
        (node.rsplit(":", 1)[-1], expected),
        "postgres",
        "SELECT count(*)::text,bool_and(substream = %s)::text "
        "FROM pg_subscription WHERE subname LIKE 'fmmr_%%'" % expected,
        "返回 2|true", rows_equal([["2", "true"]]), node=node)


CASE = {
    "id": "mmr.node_function_control.streaming_toggle",
    "name": "多活节点 streaming 本地及全局启停",
    "document": "多活功能测试文档.md",
    "section": "9.5 测试一",
    "group": "node_function_control",
    "fixtures": [
        "cluster",
        {"type": "mmr_streaming_parallel_guard", "node": "mmr:mmr1",
         "nodes": MMR_NODES, "node_name": TARGET_NODE},
    ],
    "requirements": {
        "plugins": ["fdd_mmr"], "groups": ["mmr"],
        "writable_node": True, "node": "mmr:mmr1",
    },
    "evidence_nodes": MMR_NODES,
    "prerequisites": [
        "三成员多活集群均为 ACTIVE，所有成员的 streaming 初始值均为 parallel。",
        "每个成员有两个 fmmr_ 多活订阅；fixture 会在任何失败路径调用产品 UDF 恢复全局 parallel。",
    ],
    "steps": [
        node_streaming_step("mmr:mmr1", "p"),
        node_streaming_step("mmr:mmr2", "p"),
        node_streaming_step("mmr:mmr3", "p"),
        subscriptions_step("mmr:mmr1", "true"),
        subscriptions_step("mmr:mmr2", "true"),
        subscriptions_step("mmr:mmr3", "true"),
        sql_step(
            "按文档仅关闭 mmr2 的 streaming", "postgres",
            "SELECT fdd.alter_node_info('streaming', 'mmr2', 'off', false)",
            "SQL 执行成功", command_succeeds(), node="mmr:mmr1"),
        node_streaming_step("mmr:mmr1", "f"),
        node_streaming_step("mmr:mmr2", "f"),
        node_streaming_step("mmr:mmr3", "f"),
        subscriptions_step("mmr:mmr1", "true"),
        subscriptions_step("mmr:mmr2", "false"),
        subscriptions_step("mmr:mmr3", "true"),
        sql_step(
            "按文档仅将 mmr2 的 streaming 恢复为 parallel", "postgres",
            "SELECT fdd.alter_node_info('streaming', 'mmr2', 'parallel', false)",
            "SQL 执行成功", command_succeeds(), node="mmr:mmr1"),
        subscriptions_step("mmr:mmr2", "true"),
        sql_step(
            "按文档在所有成员关闭 streaming", "postgres",
            "SELECT fdd.alter_node_info('streaming', 'mmr2', 'off', true)",
            "SQL 执行成功", command_succeeds(), node="mmr:mmr1"),
        node_streaming_step("mmr:mmr1", "f"),
        node_streaming_step("mmr:mmr2", "f"),
        node_streaming_step("mmr:mmr3", "f"),
        subscriptions_step("mmr:mmr1", "false"),
        subscriptions_step("mmr:mmr2", "false"),
        subscriptions_step("mmr:mmr3", "false"),
        sql_step(
            "恢复所有成员的 streaming=parallel", "postgres",
            "SELECT fdd.alter_node_info('streaming', 'mmr2', 'parallel', true)",
            "SQL 执行成功", command_succeeds(), node="mmr:mmr1"),
        node_streaming_step("mmr:mmr1", "p"),
        node_streaming_step("mmr:mmr2", "p"),
        node_streaming_step("mmr:mmr3", "p"),
        subscriptions_step("mmr:mmr1", "true"),
        subscriptions_step("mmr:mmr2", "true"),
        subscriptions_step("mmr:mmr3", "true"),
    ],
    "teardown": "用例显式恢复全局 parallel；fixture 在任意失败路径再次通过产品 UDF 恢复 fdd.mmr_node 和 pg_subscription。",
}
