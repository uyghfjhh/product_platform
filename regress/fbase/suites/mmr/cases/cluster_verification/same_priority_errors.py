from framework.assertions import output_contains_text, rows_equal
from framework.steps import sql_step


TARGET = "mmr:mmr2"
MESSAGE = "NODEID:2,NODESTATE:CREATED,MMR_VERSION:1-1,CHECKSUM:"
CHECK_SQL = (
    "WITH message AS (SELECT '%s'::text AS value) "
    "SELECT checked.real_sate,checked.errorno::text,checked.err_msg "
    "FROM message CROSS JOIN LATERAL "
    "fdd.check_node_info((value || md5(value))::cstring) AS checked"
) % MESSAGE


CASE = {
    "id": "mmr.cluster_verification.same_priority_errors",
    "name": "多活集群校验同级错误并列显示",
    "document": "多活功能测试文档.md",
    "section": "5.1.2",
    "group": "cluster_verification",
    "fixtures": ["cluster"],
    "requirements": {
        "clusters": ["mmr"], "plugins": ["fdd_mmr"], "groups": ["mmr"],
        "writable_node": True, "node": TARGET,
    },
    "evidence_nodes": [TARGET],
    "prerequisites": [
        "隔离两成员多活集群 healthy，mmr2 的本地 node_id 为 2 且状态为 ACTIVE。",
        "测试消息使用产品 fdd.check_node_info 协议，附带正确 MD5；不改动节点元数据或二进制版本。",
    ],
    "steps": [
        sql_step(
            "确认 mmr2 的实际节点状态为 ACTIVE", "postgres",
            "SELECT node_id::text,node_state::text FROM fdd.mmr_node "
            "WHERE node_id=(SELECT node_id FROM fdd.mmr_local_node)",
            "返回 2|ACTIVE", rows_equal([["2", "ACTIVE"]]), node=TARGET),
        sql_step(
            "构造状态与版本均不满足的校验消息", "postgres",
            "SELECT '%s'::text || md5('%s')" % (MESSAGE, MESSAGE),
            "返回含 NODESTATE:CREATED、MMR_VERSION:1-1 与正确 CHECKSUM 的消息",
            output_contains_text("NODESTATE:CREATED", "MMR_VERSION:1-1"), node=TARGET),
        sql_step(
            "执行校验并展示同优先级的状态和版本错误", "postgres", CHECK_SQL,
            "同时显示节点状态不一致和多活版本不低于 1.8000",
            output_contains_text("节点状态不一致", "多活版本不低于1.8000"), node=TARGET),
        sql_step(
            "精确确认同一次校验返回两个不同错误码", "postgres",
            "WITH message AS (SELECT '%s'::text AS value), result AS ("
            "SELECT errorno FROM message CROSS JOIN LATERAL "
            "fdd.check_node_info((value || md5(value))::cstring)) "
            "SELECT string_agg(errorno::text,',' ORDER BY errorno),count(*)::text FROM result" % MESSAGE,
            "返回 2,4|2", rows_equal([["2,4", "2"]]), node=TARGET),
        sql_step(
            "确认协议级校验未改变集群节点状态", "postgres",
            "SELECT node_id::text,node_state::text FROM fdd.mmr_node "
            "WHERE node_id=(SELECT node_id FROM fdd.mmr_local_node)",
            "返回 2|ACTIVE", rows_equal([["2", "ACTIVE"]]), node=TARGET),
    ],
    "teardown": "仅调用只读校验 UDF；不产生需要清理的多活元数据或复制对象。",
}
