from framework.assertions import rows_equal, sql_fails
from framework.steps import sql_step


SEQUENCE = "fbase_regress_mmr_gseq_set_{run_id}"
SEQUENCE_REF = "public.%s" % SEQUENCE
SEQUENCE_ARRAY = "ARRAY['%s'::regclass]" % SEQUENCE_REF


CASE = {
    "id": "mmr.global_sequence.increment_offset_settings",
    "name": "步进和偏移全局序列 node_count 设置",
    "document": "多活功能测试文档.md",
    "section": "9.6.2.3 测试一,9.6.2.3 测试三",
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
        "fixture 在异常路径删除全局元数据、复位增量并删除三个成员的测试序列。",
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
            "转换为 node_count=3 的全局序列", "postgres",
            "SELECT fdd.add_global_seq('%s'::regclass, 3, true)::text" % SEQUENCE_REF,
            "返回 true", rows_equal([["true"]]), node="mmr:mmr1"),
        sql_step(
            "按文档拒绝小于 ACTIVE 节点数的 node_count", "postgres",
            "SELECT fdd.set_global_seq(%s, 2, NULL, NULL)" % SEQUENCE_ARRAY,
            "SQL 执行失败，错误包含 node_count cannot be smaller than the number of ACTIVE nodes",
            sql_fails("node_count cannot be smaller than the number of ACTIVE nodes"), node="mmr:mmr1"),
        sql_step(
            "按文档增加 node_count 并预分配节点 id", "postgres",
            "SELECT fdd.set_global_seq(%s, 4, NULL, NULL)::text" % SEQUENCE_ARRAY,
            "返回 true", rows_equal([["true"]]), node="mmr:mmr1"),
        sql_step(
            "确认 mmr1 的 node_count=4、包含零值预分配项且序列待刷新", "postgres",
            "SELECT node_count::text,(array_length(node_maximum, 1) = 4)::text,"
            "EXISTS (SELECT 1 FROM unnest(node_maximum) AS value WHERE value LIKE '%%:0')::text,"
            "seq_state::text FROM fdd.mmr_global_sequence "
            "WHERE seq_name = '%s'::regclass" % SEQUENCE_REF,
            "返回 4|true|true|i", rows_equal([["4", "true", "true", "i"]]), node="mmr:mmr1"),
        sql_step(
            "按文档将 node_count 恢复为三个成员", "postgres",
            "SELECT fdd.set_global_seq(%s, 3, NULL, NULL)::text" % SEQUENCE_ARRAY,
            "返回 true", rows_equal([["true"]]), node="mmr:mmr1"),
        sql_step(
            "确认三个成员均已恢复 node_count=3", "postgres",
            "SELECT count(*)::text,bool_and(node_count = 3)::text,"
            "bool_and(array_length(node_maximum, 1) = 3)::text "
            "FROM fdd.mmr_global_sequence WHERE seq_name = '%s'::regclass" % SEQUENCE_REF,
            "返回 1|true|true", rows_equal([["1", "true", "true"]]), node="mmr:mmr1"),
        sql_step(
            "按文档误设更大的 node_count=5", "postgres",
            "SELECT fdd.set_global_seq(%s, 5, NULL, NULL)::text" % SEQUENCE_ARRAY,
            "返回 true", rows_equal([["true"]]), node="mmr:mmr1"),
        sql_step(
            "确认 node_count=5 后序列状态待刷新", "postgres",
            "SELECT node_count::text,(array_length(node_maximum, 1) = 5)::text,"
            "seq_state::text FROM fdd.mmr_global_sequence "
            "WHERE seq_name = '%s'::regclass" % SEQUENCE_REF,
            "返回 5|true|i", rows_equal([["5", "true", "i"]]), node="mmr:mmr1"),
        sql_step(
            "按文档恢复原 node_count=3", "postgres",
            "SELECT fdd.set_global_seq(%s, 3, NULL, NULL)::text" % SEQUENCE_ARRAY,
            "返回 true", rows_equal([["true"]]), node="mmr:mmr1"),
        sql_step(
            "确认恢复步长后元数据状态为 d", "postgres",
            "SELECT node_count::text,(array_length(node_maximum, 1) = 3)::text,"
            "seq_state::text FROM fdd.mmr_global_sequence "
            "WHERE seq_name = '%s'::regclass" % SEQUENCE_REF,
            "返回 3|true|d", rows_equal([["3", "true", "d"]]), node="mmr:mmr1"),
        sql_step(
            "删除 node_count 设置测试序列", "postgres",
            "WITH result AS (SELECT node_name,success,result FROM "
            "fdd.run_on_all_nodes('DROP SEQUENCE IF EXISTS %s')) "
            "SELECT count(*)::text,bool_and(success)::text,"
            "bool_and(result = 'DROP SEQUENCE')::text FROM result" % SEQUENCE_REF,
            "返回 3|true|true", rows_equal([["3", "true", "true"]]), node="mmr:mmr1"),
    ],
    "teardown": "用例显式删除序列；fixture 在失败路径通过 fdd.delete_global_seq 复位元数据和增量后删除序列。",
}
