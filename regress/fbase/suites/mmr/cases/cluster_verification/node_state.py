from framework.assertions import command_succeeds, output_contains_text, rows_equal
from framework.steps import sql_step


HEALTHY_CLUSTER_SQL = """
SELECT count(*)::text,
       bool_and(nodestate = 'ACTIVE')::text,
       bool_and(real_nodestate = 'ACTIVE')::text,
       bool_and(is_abnormal = 'OK')::text,
       bool_and(detail = 'OK')::text
FROM fdd.show_node_info(true, false)
"""


CASE = {
    "id": "mmr.cluster_verification.node_state",
    "name": "多活集群校验节点状态不一致检测",
    "document": "多活功能测试文档.md",
    "section": "5.1.3",
    "group": "cluster_verification",
    "fixtures": [
        "cluster",
        {"type": "mmr_node_state_guard", "node": "mmr:mmr1", "node_id": 2,
         "expected_state": "ACTIVE"},
    ],
    "requirements": {
        "plugins": ["fdd_mmr"], "groups": ["mmr"],
        "writable_node": True, "node": "mmr:mmr1",
    },
    "evidence_nodes": ["mmr:mmr1", "mmr:mmr2", "mmr:mmr3"],
    "prerequisites": [
        "三成员多活集群已完成 join，三个成员主库均为 ACTIVE。",
        "mmr1 上 node_id=2 的初始 node_state 为 ACTIVE；fixture 会在任何结束路径恢复它。",
    ],
    "steps": [
        sql_step(
            "确认 mmr1 中节点 2 的初始状态为 ACTIVE", "postgres",
            "SELECT node_id::text,node_state::text FROM fdd.mmr_node WHERE node_id = 2",
            "返回 2|ACTIVE", rows_equal([["2", "ACTIVE"]]), node="mmr:mmr1"),
        sql_step(
            "在 mmr1 将节点 2 元数据状态改为 CREATED", "postgres",
            "UPDATE fdd.mmr_node SET node_state = 'CREATED' WHERE node_id = 2",
            "UPDATE 成功", command_succeeds(), node="mmr:mmr1"),
        sql_step(
            "确认 mmr1 中节点 2 元数据已变为 CREATED", "postgres",
            "SELECT node_id::text,node_state::text FROM fdd.mmr_node WHERE node_id = 2",
            "返回 2|CREATED", rows_equal([["2", "CREATED"]]), node="mmr:mmr1"),
        sql_step(
            "mmr1 校验节点状态不一致并展示完整结果", "postgres",
            "SELECT nodeid,nodename,nodestate,real_nodestate,is_abnormal,detail "
            "FROM fdd.show_node_info(true, false) WHERE nodeid = 2",
            "显示 CREATED/ACTIVE、ERR_NODE_STATE 和节点状态不一致",
            output_contains_text("CREATED", "ACTIVE", "ERR_NODE_STATE", "节点状态不一致"),
            node="mmr:mmr1"),
        sql_step(
            "mmr1 精确确认节点 2 返回 ERR_NODE_STATE", "postgres",
            "SELECT nodestate,real_nodestate,is_abnormal,detail "
            "FROM fdd.show_node_info(true, false) WHERE nodeid = 2",
            "返回 CREATED|ACTIVE|ERR_NODE_STATE|节点状态不一致",
            rows_equal([["CREATED", "ACTIVE", "ERR_NODE_STATE", "节点状态不一致"]]),
            node="mmr:mmr1"),
        sql_step(
            "mmr3 校验同一元数据差异并展示结果", "postgres",
            "SELECT nodeid,nodename,nodestate,real_nodestate,is_abnormal,detail "
            "FROM fdd.show_node_info(true, false) "
            "WHERE is_abnormal = 'DIFF_ClUSTER' AND detail = '元数据不一致(mmr_node)'",
            "至少显示一条 DIFF_ClUSTER 元数据不一致(mmr_node) 结果",
            output_contains_text("DIFF_ClUSTER", "元数据不一致(mmr_node)"), node="mmr:mmr3"),
        sql_step(
            "mmr3 精确确认存在 mmr_node 元数据不一致", "postgres",
            "SELECT EXISTS (SELECT 1 FROM fdd.show_node_info(true, false) "
            "WHERE is_abnormal = 'DIFF_ClUSTER' "
            "AND detail = '元数据不一致(mmr_node)')::text",
            "返回 true", rows_equal([["true"]]), node="mmr:mmr3"),
        sql_step(
            "恢复 mmr1 中节点 2 元数据状态为 ACTIVE", "postgres",
            "UPDATE fdd.mmr_node SET node_state = 'ACTIVE' WHERE node_id = 2",
            "UPDATE 成功", command_succeeds(), node="mmr:mmr1"),
        sql_step(
            "确认恢复后 mmr1 全集群校验重新健康", "postgres", HEALTHY_CLUSTER_SQL,
            "返回 3|true|true|true|true",
            rows_equal([["3", "true", "true", "true", "true"]]), node="mmr:mmr1"),
    ],
    "teardown": "用例步骤恢复 node_id=2 为 ACTIVE；mmr_node_state_guard 另保存原值并在任何失败路径无条件恢复。",
}
