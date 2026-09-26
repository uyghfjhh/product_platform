from framework.assertions import command_succeeds, rows_equal
from framework.steps import sql_step


SEQUENCE = "fbase_regress_mmr_gseq_ref_{run_id}"
SEQUENCE_REF = "public.%s" % SEQUENCE
SEQUENCE_ARRAY = "ARRAY['%s'::regclass]" % SEQUENCE_REF


def increment_step(node, expected):
    return sql_step(
        "确认 %s 中测试序列步长为 %s" % (node.rsplit(":", 1)[-1], expected),
        "postgres",
        "SELECT seqincrement::text FROM pg_sequence "
        "WHERE seqrelid = '%s'::regclass" % SEQUENCE_REF,
        "返回 %s" % expected, rows_equal([[expected]]), node=node)


CASE = {
    "id": "mmr.global_sequence.increment_offset_refresh",
    "name": "异步步进和偏移序列刷新",
    "document": "多活功能测试文档.md",
    "section": "9.6.2.4 测试一,9.6.2.4 测试二",
    "group": "global_sequence",
    "fixtures": [
        "cluster",
        {"type": "mmr_global_sequences_empty",
         "nodes": ["mmr:mmr1", "mmr:mmr2", "mmr:mmr3"]},
        {"type": "mmr_global_sequence_probe", "node": "mmr:mmr1",
         "nodes": ["mmr:mmr1", "mmr:mmr2", "mmr:mmr3"], "name": SEQUENCE},
    ],
    "requirements": {
        "plugins": ["fdd_mmr"], "groups": ["mmr"],
        "writable_node": True, "node": "mmr:mmr1",
    },
    "evidence_nodes": ["mmr:mmr1", "mmr:mmr2", "mmr:mmr3"],
    "prerequisites": [
        "三个成员均为 ACTIVE，且不存在本 run_id 对应的同名序列。",
        "三个成员 fdd.mmr_global_sequence 为空；否则拒绝执行文档的全量刷新，避免影响既有序列。",
        "异步转换不立即修改序列步长；fdd.refresh_global_seq 才会刷新实际序列和元数据状态。",
    ],
    "steps": [
        sql_step(
            "在所有成员创建同名普通序列", "postgres",
            "WITH result AS (SELECT node_name,success,result FROM "
            "fdd.run_on_all_nodes('CREATE SEQUENCE %s START WITH 1 INCREMENT BY 1')) "
            "SELECT count(*)::text,bool_and(success)::text,"
            "bool_and(result = 'CREATE SEQUENCE')::text FROM result" % SEQUENCE_REF,
            "返回 3|true|true", rows_equal([["3", "true", "true"]]), node="mmr:mmr1"),
        sql_step(
            "按文档异步转换普通序列", "postgres",
            "SELECT fdd.add_global_seq('%s'::regclass, 3, false)::text" % SEQUENCE_REF,
            "返回 true", rows_equal([["true"]]), node="mmr:mmr1"),
        sql_step(
            "确认异步转换后元数据状态为 i", "postgres",
            "SELECT node_count::text,seq_state::text FROM fdd.mmr_global_sequence "
            "WHERE seq_name = '%s'::regclass" % SEQUENCE_REF,
            "返回 3|i", rows_equal([["3", "i"]]), node="mmr:mmr1"),
        increment_step("mmr:mmr1", "1"),
        increment_step("mmr:mmr2", "1"),
        increment_step("mmr:mmr3", "1"),
        sql_step(
            "按文档刷新指定异步全局序列", "postgres",
            "SELECT fdd.refresh_global_seq(%s)" % SEQUENCE_ARRAY,
            "SQL 执行成功", command_succeeds(), node="mmr:mmr1"),
        sql_step(
            "确认刷新后元数据状态为 d", "postgres",
            "SELECT node_count::text,seq_state::text FROM fdd.mmr_global_sequence "
            "WHERE seq_name = '%s'::regclass" % SEQUENCE_REF,
            "返回 3|d", rows_equal([["3", "d"]]), node="mmr:mmr1"),
        increment_step("mmr:mmr1", "3"),
        increment_step("mmr:mmr2", "3"),
        increment_step("mmr:mmr3", "3"),
        sql_step(
            "按文档将全部全局序列 node_count 设置为 4", "postgres",
            "SELECT fdd.set_global_seq(NULL, 4, NULL, NULL)::text",
            "返回 true", rows_equal([["true"]]), node="mmr:mmr1"),
        sql_step(
            "确认设置后测试序列有四项分配且状态待刷新", "postgres",
            "SELECT node_count::text,(array_length(node_maximum, 1) = 4)::text,"
            "seq_state::text FROM fdd.mmr_global_sequence "
            "WHERE seq_name = '%s'::regclass" % SEQUENCE_REF,
            "返回 4|true|i", rows_equal([["4", "true", "i"]]), node="mmr:mmr1"),
        sql_step(
            "按文档刷新全部全局序列", "postgres",
            "SELECT fdd.refresh_global_seq(NULL)",
            "SQL 执行成功", command_succeeds(), node="mmr:mmr1"),
        sql_step(
            "确认全量刷新后测试序列状态为 d", "postgres",
            "SELECT node_count::text,seq_state::text FROM fdd.mmr_global_sequence "
            "WHERE seq_name = '%s'::regclass" % SEQUENCE_REF,
            "返回 4|d", rows_equal([["4", "d"]]), node="mmr:mmr1"),
        increment_step("mmr:mmr1", "4"),
        increment_step("mmr:mmr2", "4"),
        increment_step("mmr:mmr3", "4"),
        sql_step(
            "删除异步刷新测试序列", "postgres",
            "WITH result AS (SELECT node_name,success,result FROM "
            "fdd.run_on_all_nodes('DROP SEQUENCE IF EXISTS %s')) "
            "SELECT count(*)::text,bool_and(success)::text,"
            "bool_and(result = 'DROP SEQUENCE')::text FROM result" % SEQUENCE_REF,
            "返回 3|true|true", rows_equal([["3", "true", "true"]]), node="mmr:mmr1"),
    ],
    "teardown": "用例显式删除序列；fixture 在失败路径先清理全局序列元数据和增量，再删除三成员测试序列。",
}
