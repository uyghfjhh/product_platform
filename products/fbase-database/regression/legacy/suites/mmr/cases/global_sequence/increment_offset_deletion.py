from framework.assertions import command_succeeds, rows_equal
from framework.steps import sql_step


SEQUENCE = "fbase_regress_mmr_gseq_del_{run_id}"
SEQUENCE_REF = "public.%s" % SEQUENCE


def sequence_increment_step(node):
    return sql_step(
        "确认 %s 中测试序列步长已重置为 1" % node.rsplit(":", 1)[-1],
        "postgres",
        "SELECT seqincrement::text FROM pg_sequence "
        "WHERE seqrelid = '%s'::regclass" % SEQUENCE_REF,
        "返回 1", rows_equal([["1"]]), node=node)


CASE = {
    "id": "mmr.global_sequence.increment_offset_deletion",
    "name": "步进和偏移全局序列删除及步长复位",
    "document": "多活功能测试文档.md",
    "section": "9.6.2.2",
    "group": "global_sequence",
    "fixtures": [
        "cluster",
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
        "fixture 在异常路径先删除全局序列元数据并重置步长，再删除三个成员的测试序列。",
    ],
    "steps": [
        sql_step(
            "在所有多活成员创建同名普通序列", "postgres",
            "WITH command_result AS (SELECT node_name,success,result FROM "
            "fdd.run_on_all_nodes('CREATE SEQUENCE %s START WITH 1 INCREMENT BY 1')) "
            "SELECT count(*)::text,bool_and(success)::text,"
            "bool_and(result = 'CREATE SEQUENCE')::text FROM command_result" % SEQUENCE_REF,
            "返回 3|true|true", rows_equal([["3", "true", "true"]]), node="mmr:mmr1"),
        sql_step(
            "将测试序列转换为步进和偏移全局序列", "postgres",
            "SELECT fdd.add_global_seq('%s'::regclass, 3, true)::text" % SEQUENCE_REF,
            "返回 true", rows_equal([["true"]]), node="mmr:mmr1"),
        sql_step(
            "确认转换后三个成员均有全局序列元数据", "postgres",
            "SELECT count(*)::text,bool_and(seq_state = 'd')::text "
            "FROM fdd.mmr_global_sequence WHERE seq_name = '%s'::regclass" % SEQUENCE_REF,
            "返回 1|true", rows_equal([["1", "true"]]), node="mmr:mmr1"),
        sql_step(
            "按文档立即删除全局序列元数据并重置步长", "postgres",
            "SELECT fdd.delete_global_seq('%s'::regclass, true, true)::text" % SEQUENCE_REF,
            "返回 true", rows_equal([["true"]]), node="mmr:mmr1"),
        sql_step(
            "确认 mmr1 的测试序列元数据已删除", "postgres",
            "SELECT count(*)::text FROM fdd.mmr_global_sequence "
            "WHERE seq_name = '%s'::regclass" % SEQUENCE_REF,
            "返回 0", rows_equal([["0"]]), node="mmr:mmr1"),
        sql_step(
            "确认 mmr2 的测试序列元数据已删除", "postgres",
            "SELECT count(*)::text FROM fdd.mmr_global_sequence "
            "WHERE seq_name = '%s'::regclass" % SEQUENCE_REF,
            "返回 0", rows_equal([["0"]]), node="mmr:mmr2"),
        sql_step(
            "确认 mmr3 的测试序列元数据已删除", "postgres",
            "SELECT count(*)::text FROM fdd.mmr_global_sequence "
            "WHERE seq_name = '%s'::regclass" % SEQUENCE_REF,
            "返回 0", rows_equal([["0"]]), node="mmr:mmr3"),
        sequence_increment_step("mmr:mmr1"),
        sequence_increment_step("mmr:mmr2"),
        sequence_increment_step("mmr:mmr3"),
        sql_step(
            "删除三个成员的普通序列测试对象", "postgres",
            "WITH command_result AS (SELECT node_name,success,result FROM "
            "fdd.run_on_all_nodes('DROP SEQUENCE IF EXISTS %s')) "
            "SELECT count(*)::text,bool_and(success)::text,"
            "bool_and(result = 'DROP SEQUENCE')::text FROM command_result" % SEQUENCE_REF,
            "返回 3|true|true", rows_equal([["3", "true", "true"]]), node="mmr:mmr1"),
    ],
    "teardown": "用例显式删除序列；mmr_global_sequence_probe 在任何失败路径按删除元数据、重置步长、删除序列的顺序清理。",
}
