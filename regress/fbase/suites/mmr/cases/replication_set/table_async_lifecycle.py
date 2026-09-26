from framework.assertions import command_succeeds, rows_equal
from framework.steps import sql_step

SET_NAME = "fbase_r_mmr_rt_{run_id}"
TABLE = "fbase_r_mmr_rt_{run_id}"
TABLE_REF = "public.%s" % TABLE
NODES = ["mmr:mmr1", "mmr:mmr2", "mmr:mmr3"]

CASE = {
    "id": "mmr.replication_set.table_async_lifecycle", "name": "复制集手动加表及异步移表",
    "document": "多活功能测试文档.md", "section": "3.5 测试三,3.6 测试一", "group": "replication_set",
    "fixtures": ["cluster", {"type": "mmr_replication_sets_empty", "node": "mmr:mmr1", "names": [SET_NAME]},
                 {"type": "mmr_remote_sql_tables", "nodes": NODES, "tables": [TABLE]},
                 {"type": "mmr_async_set_mode_recovery", "nodes": NODES}],
    "requirements": {"plugins": ["fdd_mmr"], "groups": ["mmr"], "writable_node": True, "node": "mmr:mmr1"},
    "evidence_nodes": NODES,
    "prerequisites": ["三个成员均 ACTIVE；使用 copy_data=false 避开当前 two_phase=true 的产品限制。"],
    "steps": [
        sql_step("创建不自动绑定已有表的全局复制集", "postgres",
                 "SELECT fdd.create_replication_set('%s',true,true,true,true,false,false,false,false,true)" % SET_NAME,
                 "SQL 执行成功", command_succeeds(), node="mmr:mmr1"),
        sql_step("在所有成员创建同构测试表", "postgres",
                 "WITH r AS (SELECT node_name,success,result FROM fdd.run_on_all_nodes('CREATE TABLE %s(id int PRIMARY KEY)')) SELECT count(*)::text,bool_and(success)::text,bool_and(result='CREATE TABLE')::text FROM r" % TABLE_REF,
                 "返回 3|true|true", rows_equal([["3","true","true"]]), node="mmr:mmr1"),
        sql_step("按文档手动异步添加测试表且不复制存量", "postgres",
                 "SELECT fdd.replication_set_add_table('%s'::regclass,'%s',false,false)" % (TABLE_REF, SET_NAME),
                 "SQL 执行成功", command_succeeds(), node="mmr:mmr1"),
        sql_step("确认映射表记录处于等待状态 w", "postgres",
                 "SELECT set_sync_state::text,set_iscopydata::text FROM fdd.mmr_replication_set_table t JOIN fdd.mmr_replication_set s ON s.set_id=t.set_id WHERE s.set_name='%s' AND t.set_reloid='%s'::regclass" % (SET_NAME,TABLE_REF),
                 "返回 w|false", rows_equal([["w","false"]]), node="mmr:mmr1"),
        sql_step("按文档异步移除测试表", "postgres",
                 "SELECT fdd.replication_set_remove_table('%s'::regclass,'%s',false)" % (TABLE_REF,SET_NAME),
                 "SQL 执行成功", command_succeeds(), node="mmr:mmr1"),
        sql_step("确认异步移除记录状态为 r", "postgres",
                 "SELECT set_sync_state::text FROM fdd.mmr_replication_set_table t JOIN fdd.mmr_replication_set s ON s.set_id=t.set_id WHERE s.set_name='%s' AND t.set_reloid='%s'::regclass" % (SET_NAME,TABLE_REF),
                 "返回 r", rows_equal([["r"]]), node="mmr:mmr1"),
        sql_step("删除复制集测试对象", "postgres", "SELECT fdd.drop_replication_set('%s')" % SET_NAME,
                 "SQL 执行成功", command_succeeds(), node="mmr:mmr1"),
    ],
    "teardown": "fixture 删除复制集和三成员表，并调用 fdd.check_async_record() 恢复 set_mode。",
}
