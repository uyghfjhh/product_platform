from framework.assertions import command_succeeds, rows_equal
from framework.steps import sql_step


SCHEMA_ONE = "fbase_r_mmr_s1_{run_id}"
SCHEMA_TWO = "fbase_r_mmr_s2_{run_id}"
SEQUENCE = "s1"
SEQUENCE_ONE = "%s.%s" % (SCHEMA_ONE, SEQUENCE)
SEQUENCE_TWO = "%s.%s" % (SCHEMA_TWO, SEQUENCE)
MMR_NODES = ["mmr:mmr1", "mmr:mmr2", "mmr:mmr3"]


def all_node_command(title, command, expected_tag):
    return sql_step(
        title, "postgres",
        "WITH result AS (SELECT node_name,success,result FROM "
        "fdd.run_on_all_nodes('%s')) "
        "SELECT count(*)::text,bool_and(success)::text,"
        "bool_and(result = '%s')::text FROM result" % (command, expected_tag),
        "返回 3|true|true", rows_equal([["3", "true", "true"]]), node="mmr:mmr1")


def metadata_step(title, sequence_ref, node_count):
    return sql_step(
        title, "postgres",
        "SELECT count(*)::text,bool_and(node_count = %s)::text,"
        "bool_and(array_length(node_maximum, 1) = %s)::text,"
        "bool_and(seq_state = 'd')::text FROM fdd.mmr_global_sequence "
        "WHERE seq_name = '%s'::regclass" % (node_count, node_count, sequence_ref),
        "返回 1|true|true|true", rows_equal([["1", "true", "true", "true"]]),
        node="mmr:mmr1")


CASE = {
    "id": "mmr.global_sequence.increment_offset_schema_names",
    "name": "不同 schema 同名序列转换为全局序列",
    "document": "多活功能测试文档.md",
    "section": "9.6.2.1 测试二",
    "group": "global_sequence",
    "fixtures": [
        "cluster",
        {"type": "mmr_schemas_empty", "nodes": MMR_NODES,
         "schemas": [SCHEMA_ONE, SCHEMA_TWO]},
        {"type": "mmr_global_sequence_probe", "node": "mmr:mmr1", "nodes": MMR_NODES,
         "schema": SCHEMA_ONE, "name": SEQUENCE},
        {"type": "mmr_global_sequence_probe", "node": "mmr:mmr1", "nodes": MMR_NODES,
         "schema": SCHEMA_TWO, "name": SEQUENCE},
    ],
    "requirements": {
        "plugins": ["fdd_mmr"], "groups": ["mmr"],
        "writable_node": True, "node": "mmr:mmr1",
    },
    "evidence_nodes": MMR_NODES,
    "prerequisites": [
        "三个成员均为 ACTIVE，且所有成员不存在本 run_id 对应的两个测试 schema。",
        "fixture 在失败路径先删除两个全局序列元数据和序列，再删除三个成员的测试 schema。",
    ],
    "steps": [
        all_node_command("在所有成员创建第一个测试 schema", "CREATE SCHEMA %s" % SCHEMA_ONE,
                         "CREATE SCHEMA"),
        all_node_command("在所有成员创建第二个测试 schema", "CREATE SCHEMA %s" % SCHEMA_TWO,
                         "CREATE SCHEMA"),
        all_node_command("在所有成员创建第一个 schema 的同名序列",
                         "CREATE SEQUENCE %s" % SEQUENCE_ONE, "CREATE SEQUENCE"),
        all_node_command("在所有成员创建第二个 schema 的同名序列",
                         "CREATE SEQUENCE %s" % SEQUENCE_TWO, "CREATE SEQUENCE"),
        sql_step(
            "按文档转换第一个 schema 的 s1，node_count=3", "postgres",
            "SELECT fdd.add_global_seq('%s'::regclass, 3, true)::text" % SEQUENCE_ONE,
            "返回 true", rows_equal([["true"]]), node="mmr:mmr1"),
        metadata_step("确认第一个 schema 的 s1 元数据独立且已完成", SEQUENCE_ONE, 3),
        sql_step(
            "按文档转换第二个 schema 的同名 s1，node_count=4", "postgres",
            "SELECT fdd.add_global_seq('%s'::regclass, 4, true)::text" % SEQUENCE_TWO,
            "返回 true", rows_equal([["true"]]), node="mmr:mmr1"),
        metadata_step("确认第二个 schema 的 s1 元数据独立且已完成", SEQUENCE_TWO, 4),
        sql_step(
            "确认两个 schema 的同名 s1 同时保留各自元数据", "postgres",
            "SELECT count(*)::text,bool_and(seq_name::text IN ('%s','%s'))::text "
            "FROM fdd.mmr_global_sequence WHERE seq_name IN "
            "('%s'::regclass,'%s'::regclass)" %
            (SEQUENCE_ONE, SEQUENCE_TWO, SEQUENCE_ONE, SEQUENCE_TWO),
            "返回 2|true", rows_equal([["2", "true"]]), node="mmr:mmr1"),
        sql_step(
            "删除第一个 schema 的全局序列元数据并重置步长", "postgres",
            "SELECT fdd.delete_global_seq('%s'::regclass, true, true)::text" % SEQUENCE_ONE,
            "返回 true", rows_equal([["true"]]), node="mmr:mmr1"),
        sql_step(
            "删除第二个 schema 的全局序列元数据并重置步长", "postgres",
            "SELECT fdd.delete_global_seq('%s'::regclass, true, true)::text" % SEQUENCE_TWO,
            "返回 true", rows_equal([["true"]]), node="mmr:mmr1"),
        sql_step(
            "确认两个同名序列的全局元数据均已删除", "postgres",
            "SELECT count(*)::text FROM fdd.mmr_global_sequence WHERE seq_name IN "
            "('%s'::regclass,'%s'::regclass)" % (SEQUENCE_ONE, SEQUENCE_TWO),
            "返回 0", rows_equal([["0"]]), node="mmr:mmr1"),
        sql_step(
            "删除两个 schema 的测试序列", "postgres",
            "WITH result AS (SELECT node_name,success,result FROM fdd.run_on_all_nodes("
            "'DROP SEQUENCE IF EXISTS %s; DROP SEQUENCE IF EXISTS %s')) "
            "SELECT count(*)::text,bool_and(success)::text,"
            "bool_and(result LIKE 'DROP SEQUENCE%%')::text FROM result" % (SEQUENCE_ONE, SEQUENCE_TWO),
            "返回 3|true|true", rows_equal([["3", "true", "true"]]), node="mmr:mmr1"),
        all_node_command("在所有成员删除第一个测试 schema", "DROP SCHEMA %s" % SCHEMA_ONE,
                         "DROP SCHEMA"),
        all_node_command("在所有成员删除第二个测试 schema", "DROP SCHEMA %s" % SCHEMA_TWO,
                         "DROP SCHEMA"),
    ],
    "teardown": "显式删除两个序列和 schema；fixtures 在失败路径按全局元数据、序列、schema 顺序清理。",
}
