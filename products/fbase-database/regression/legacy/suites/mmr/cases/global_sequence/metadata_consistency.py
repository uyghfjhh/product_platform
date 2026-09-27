from framework.assertions import command_succeeds, rows_equal, sql_fails
from framework.steps import sql_step


SEQUENCE = "fbase_r_mmr_gseq_meta_{run_id}"
SEQUENCE_REF = "public.%s" % SEQUENCE
NODES = ["mmr:mmr1", "mmr:mmr2", "mmr:mmr3"]


def health_step(title):
    return sql_step(title, "postgres",
                    "SELECT count(*)::text,bool_and(is_abnormal='OK')::text,bool_and(detail='OK')::text FROM fdd.show_node_info(true,false)",
                    "返回 3|true|true", rows_equal([["3", "true", "true"]]), node="mmr:mmr1")


CASE = {
    "id": "mmr.global_sequence.metadata_consistency", "name": "全局序列元数据一致性校验",
    "document": "多活功能测试文档.md", "section": "9.6.3.1", "group": "global_sequence",
    "fixtures": ["cluster", {"type": "mmr_global_sequence_probe", "node": "mmr:mmr1", "nodes": NODES, "name": SEQUENCE}],
    "requirements": {"plugins": ["fdd_mmr"], "groups": ["mmr"], "writable_node": True, "node": "mmr:mmr1"},
    "evidence_nodes": NODES,
    "prerequisites": ["三个成员 ACTIVE，run_id 测试序列不存在；文档的 DSN 与原字符串不同但解析后的键值相同，本用例在当前 DSN 后追加空白重建该输入。"],
    "steps": [
        sql_step("在所有成员创建同名序列", "postgres",
                 "WITH r AS (SELECT node_name,success,result FROM fdd.run_on_all_nodes('CREATE SEQUENCE %s')) SELECT count(*)::text,bool_and(success)::text,bool_and(result='CREATE SEQUENCE')::text FROM r" % SEQUENCE_REF,
                 "返回 3|true|true", rows_equal([["3", "true", "true"]]), node="mmr:mmr1"),
        sql_step("转换为全局序列", "postgres", "SELECT fdd.add_global_seq('%s'::regclass,3,true)::text" % SEQUENCE_REF,
                 "返回 true", rows_equal([["true"]]), node="mmr:mmr1"),
        health_step("确认初始全局序列元数据一致"),
        sql_step("按文档在 mmr3 删除序列制造元数据不一致", "postgres", "DROP SEQUENCE %s" % SEQUENCE_REF,
                 "DROP SEQUENCE 成功", command_succeeds(), node="mmr:mmr3"),
        sql_step("确认集群校验报告全局序列元数据不一致", "postgres",
                 "SELECT count(*)::text FROM fdd.show_node_info(true,false) WHERE detail LIKE '%mmr_global_sequence%'",
                 "返回大于 0", rows_equal([["1"]]), node="mmr:mmr1"),
        sql_step("确认不一致阻止同值节点接口更新", "postgres",
                 "SELECT fdd.alter_node_interface('mmr3',(SELECT (node_dsn || ' ')::cstring FROM fdd.mmr_node WHERE node_name='mmr3'))",
                 "SQL 失败且说明 metadata not same", sql_fails("metadata not same"), node="mmr:mmr1",
                 ),
        sql_step("按文档删除 mmr1 序列", "postgres", "DROP SEQUENCE %s" % SEQUENCE_REF,
                 "DROP SEQUENCE 成功", command_succeeds(), node="mmr:mmr1"),
        sql_step("清理 mmr1 无效全局序列元数据", "postgres", "SELECT fdd.clean_invalid_global_seq()",
                 "SQL 执行成功", command_succeeds(), node="mmr:mmr1"),
        sql_step("按文档删除 mmr2 序列", "postgres", "DROP SEQUENCE %s" % SEQUENCE_REF,
                 "DROP SEQUENCE 成功", command_succeeds(), node="mmr:mmr2"),
        sql_step("清理 mmr2 无效全局序列元数据", "postgres", "SELECT fdd.clean_invalid_global_seq()",
                 "SQL 执行成功", command_succeeds(), node="mmr:mmr2"),
        sql_step("清理 mmr3 无效全局序列元数据", "postgres", "SELECT fdd.clean_invalid_global_seq()",
                 "SQL 执行成功", command_succeeds(), node="mmr:mmr3"),
        health_step("确认清理后集群校验恢复一致"),
        sql_step("确认一致后解析相同但原字符串不同的节点接口更新成功", "postgres",
                 "SELECT fdd.alter_node_interface('mmr3',(SELECT (node_dsn || ' ')::cstring FROM fdd.mmr_node WHERE node_name='mmr3'))::text",
                 "返回 true", rows_equal([["true"]]), node="mmr:mmr1"),
    ],
    "teardown": "所有成员序列和无效元数据均在步骤中清理；fixture 仅处理异常中断。",
}
