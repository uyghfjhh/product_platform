from framework.assertions import command_succeeds, rows_equal
from framework.steps import sql_step


SEQUENCE = "fbase_r_mmr_gseq_clean_{run_id}"
SEQUENCE_REF = "public.%s" % SEQUENCE
NODES = ["mmr:mmr1", "mmr:mmr2", "mmr:mmr3"]


CASE = {
    "id": "mmr.global_sequence.invalid_metadata_cleanup",
    "name": "无效全局序列元数据清理",
    "document": "多活功能测试文档.md", "section": "9.6.2.5",
    "group": "global_sequence",
    "fixtures": ["cluster", {"type": "mmr_global_sequence_probe", "node": "mmr:mmr1", "nodes": NODES, "name": SEQUENCE}],
    "requirements": {"plugins": ["fdd_mmr"], "groups": ["mmr"], "writable_node": True, "node": "mmr:mmr1"},
    "evidence_nodes": NODES,
    "prerequisites": ["三个成员 ACTIVE，run_id 测试序列不存在；fixture 在失败路径恢复元数据和序列。"],
    "steps": [
        sql_step("在所有成员创建同名普通序列", "postgres",
                 "WITH r AS (SELECT node_name,success,result FROM fdd.run_on_all_nodes('CREATE SEQUENCE %s')) SELECT count(*)::text,bool_and(success)::text,bool_and(result='CREATE SEQUENCE')::text FROM r" % SEQUENCE_REF,
                 "返回 3|true|true", rows_equal([["3", "true", "true"]]), node="mmr:mmr1"),
        sql_step("转换为步进和偏移全局序列", "postgres", "SELECT fdd.add_global_seq('%s'::regclass,3,true)::text" % SEQUENCE_REF,
                 "返回 true", rows_equal([["true"]]), node="mmr:mmr1"),
        sql_step("确认 mmr3 存在全局序列元数据", "postgres",
                 "SELECT count(*)::text FROM fdd.mmr_global_sequence WHERE seq_name='%s'::regclass" % SEQUENCE_REF,
                 "返回 1", rows_equal([["1"]]), node="mmr:mmr3"),
        sql_step("按文档在 mmr3 手工删除序列", "postgres", "DROP SEQUENCE %s" % SEQUENCE_REF,
                 "DROP SEQUENCE 成功", command_succeeds(), node="mmr:mmr3"),
        sql_step("确认 mmr3 元数据展示无效 OID", "postgres",
                 "SELECT count(*)::text FROM fdd.mmr_global_sequence WHERE seq_name::text ~ '^[0-9]+$'",
                 "返回 1", rows_equal([["1"]]), node="mmr:mmr3"),
        sql_step("按文档清理 mmr3 无效全局序列元数据", "postgres", "SELECT fdd.clean_invalid_global_seq()",
                 "SQL 执行成功", command_succeeds(), node="mmr:mmr3"),
        sql_step("确认 mmr3 无效元数据已删除", "postgres",
                 "SELECT count(*)::text FROM fdd.mmr_global_sequence WHERE seq_name::text ~ '^[0-9]+$'",
                 "返回 0", rows_equal([["0"]]), node="mmr:mmr3"),
        sql_step("按文档重新创建 mmr3 序列供全局删除", "postgres", "CREATE SEQUENCE %s" % SEQUENCE_REF,
                 "CREATE SEQUENCE 成功", command_succeeds(), node="mmr:mmr3"),
        sql_step("从 mmr1 删除全局元数据并复位三成员序列", "postgres",
                 "SELECT fdd.delete_global_seq('%s'::regclass,true,true)::text" % SEQUENCE_REF,
                 "返回 true", rows_equal([["true"]]), node="mmr:mmr1"),
        sql_step("确认三个成员全局序列元数据均已删除", "postgres",
                 "SELECT count(*)::text FROM fdd.mmr_global_sequence WHERE seq_name::text='%s'" % SEQUENCE_REF,
                 "返回 0", rows_equal([["0"]]), node="mmr:mmr1"),
    ],
    "teardown": "用例显式恢复为普通序列元数据状态；fixture 在异常路径按产品 UDF 清理。",
}
