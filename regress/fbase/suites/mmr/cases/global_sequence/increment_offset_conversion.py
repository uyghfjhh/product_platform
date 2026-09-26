from framework.assertions import rows_equal
from framework.steps import sql_step


SEQUENCE = "fbase_regress_mmr_gseq_probe_{run_id}"
SEQUENCE_REF = "public.%s" % SEQUENCE

METADATA_SQL = (
    "SELECT count(*)::text,bool_and(node_count = 3)::text,"
    "bool_and(is_reset = false)::text,"
    "bool_and(node_maximum::text = '{1:1,2:2,3:3}')::text,"
    "bool_and(seq_state = 'd')::text "
    "FROM fdd.mmr_global_sequence WHERE seq_name = '%s'::regclass" % SEQUENCE_REF)

SEQUENCE_SQL = (
    "SELECT s.seqincrement::text,q.last_value::text,q.is_called::text "
    "FROM pg_sequence s CROSS JOIN %s q WHERE s.seqrelid = '%s'::regclass" %
    (SEQUENCE_REF, SEQUENCE_REF))


CASE = {
    "id": "mmr.global_sequence.increment_offset_conversion",
    "name": "普通序列转换为步进和偏移全局序列",
    "document": "多活功能测试文档.md",
    "section": "9.6.2.1 测试一",
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
        "三个成员均为 ACTIVE，且三个节点均不存在本 run_id 对应的同名普通序列。",
        "fdd.add_global_seq 使用文档定义的 node_count=3、immediate_sync=true 参数。",
        "fixture 在任意结束路径使用 fdd.delete_global_seq 和 DROP SEQUENCE 清理本 run_id 的对象。",
    ],
    "steps": [
        sql_step(
            "按文档在所有多活节点创建同名普通序列", "postgres",
            "WITH command_result AS ("
            "SELECT node_name,success,result FROM fdd.run_on_all_nodes("
            "'CREATE SEQUENCE %s START WITH 1 INCREMENT BY 1 NO MINVALUE NO MAXVALUE CACHE 1')) "
            "SELECT count(*)::text,bool_and(success)::text,"
            "bool_and(result = 'CREATE SEQUENCE')::text FROM command_result" % SEQUENCE_REF,
            "返回 3|true|true：三个成员均成功创建普通序列",
            rows_equal([["3", "true", "true"]]), node="mmr:mmr1"),
        sql_step(
            "按文档将普通序列转换为步进和偏移全局序列", "postgres",
            "SELECT fdd.add_global_seq('%s'::regclass, 3, true)::text" % SEQUENCE_REF,
            "返回 true",
            rows_equal([["true"]]), node="mmr:mmr1"),
        sql_step(
            "确认 mmr1 全局序列元数据、节点数和初始分配", "postgres", METADATA_SQL,
            "返回 1|true|true|true|true", rows_equal([[
                "1", "true", "true", "true", "true"]]), node="mmr:mmr1"),
        sql_step(
            "确认 mmr2 全局序列元数据、节点数和初始分配", "postgres", METADATA_SQL,
            "返回 1|true|true|true|true", rows_equal([[
                "1", "true", "true", "true", "true"]]), node="mmr:mmr2"),
        sql_step(
            "确认 mmr3 全局序列元数据、节点数和初始分配", "postgres", METADATA_SQL,
            "返回 1|true|true|true|true", rows_equal([[
                "1", "true", "true", "true", "true"]]), node="mmr:mmr3"),
        sql_step(
            "确认 mmr1 序列步长及初始值", "postgres", SEQUENCE_SQL,
            "返回 3|1|false", rows_equal([["3", "1", "false"]]), node="mmr:mmr1"),
        sql_step(
            "确认 mmr2 序列步长及初始值", "postgres", SEQUENCE_SQL,
            "返回 3|2|false", rows_equal([["3", "2", "false"]]), node="mmr:mmr2"),
        sql_step(
            "确认 mmr3 序列步长及初始值", "postgres", SEQUENCE_SQL,
            "返回 3|3|false", rows_equal([["3", "3", "false"]]), node="mmr:mmr3"),
    ],
    "teardown": "fixture 先调用 fdd.delete_global_seq(..., true, true) 删除全局元数据并复位步长，再删除三节点同名测试序列。",
}
