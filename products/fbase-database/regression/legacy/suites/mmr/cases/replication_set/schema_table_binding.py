from framework.assertions import command_succeeds, rows_equal
from framework.steps import sql_step


SET_NAME = "fbase_r_mmr_rsa_{run_id}"
SCHEMA = "fbase_r_mmr_rsa_{run_id}"
TABLE_ONE = "table1"
TABLE_TWO = "table2"
NODES = ["mmr:mmr1", "mmr:mmr2", "mmr:mmr3"]


def all_nodes_command(command, tag):
    return (
        "WITH r AS (SELECT node_name,success,result FROM fdd.run_on_all_nodes('%s')) "
        "SELECT count(*)::text,bool_and(success)::text,bool_and(result='%s')::text FROM r" %
        (command, tag))


CASE = {
    "id": "mmr.replication_set.schema_table_binding",
    "name": "复制集按 schema 批量绑定表",
    "document": "多活功能测试文档.md", "section": "3.5 测试四",
    "group": "replication_set",
    "fixtures": [
        "cluster",
        {"type": "mmr_replication_sets_empty", "node": "mmr:mmr1", "names": [SET_NAME]},
        {"type": "mmr_schemas_empty", "nodes": NODES, "schemas": [SCHEMA]},
        {"type": "mmr_async_set_mode_recovery", "nodes": NODES},
    ],
    "requirements": {"plugins": ["fdd_mmr"], "groups": ["mmr"],
                     "writable_node": True, "node": "mmr:mmr1"},
    "evidence_nodes": NODES,
    "prerequisites": ["三个成员 ACTIVE，run_id schema 和复制集均不存在。",
                      "复制集使用 copy_data=false，避开当前 two_phase=true 的存量复制限制。"],
    "steps": [
        sql_step("创建不自动绑定表的全局复制集", "postgres",
                 "SELECT fdd.create_replication_set('%s',true,true,true,true,false,false,false,false,true)" % SET_NAME,
                 "SQL 执行成功", command_succeeds(), node="mmr:mmr1"),
        sql_step("在所有成员创建测试 schema", "postgres",
                 all_nodes_command("CREATE SCHEMA %s" % SCHEMA, "CREATE SCHEMA"),
                 "返回 3|true|true", rows_equal([["3", "true", "true"]]), node="mmr:mmr1"),
        sql_step("在所有成员创建 schema 内两张同构表", "postgres",
                 all_nodes_command("CREATE TABLE %s.%s(id int PRIMARY KEY,name name); CREATE TABLE %s.%s(id int PRIMARY KEY,name name)" % (SCHEMA, TABLE_ONE, SCHEMA, TABLE_TWO), "CREATE TABLE"),
                 "返回 3|true|true", rows_equal([["3", "true", "true"]]), node="mmr:mmr1"),
        sql_step("按文档批量绑定 schema 下全部表", "postgres",
                 "SELECT fdd.replication_set_add_all_tables('%s',ARRAY['%s']::text[],false)" % (SET_NAME, SCHEMA),
                 "SQL 执行成功", command_succeeds(), node="mmr:mmr1"),
        sql_step("确认两张 schema 表均为等待状态且不复制存量", "postgres",
                 "SELECT count(*)::text,bool_and(set_sync_state='w')::text,bool_and(NOT set_iscopydata)::text FROM fdd.mmr_replication_set_table t JOIN fdd.mmr_replication_set s ON s.set_id=t.set_id WHERE s.set_name='%s' AND t.set_reloid IN ('%s.%s'::regclass,'%s.%s'::regclass)" % (SET_NAME, SCHEMA, TABLE_ONE, SCHEMA, TABLE_TWO),
                 "返回 2|true|true", rows_equal([["2", "true", "true"]]), node="mmr:mmr1"),
    ],
    "teardown": "fixture 先删除全局复制集映射，再删除三个成员的 schema，最后收敛异步状态。",
}
