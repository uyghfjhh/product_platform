from framework.assertions import command_succeeds, rows_equal
from framework.steps import sql_step


EXISTING_SET = "fbase_r_mmr_rae_{run_id}"
NEW_SET = "fbase_r_mmr_rat_{run_id}"
EXISTING_TABLE = "fbase_r_mmr_rae_t_{run_id}"
NEW_TABLE = "fbase_r_mmr_rat_t_{run_id}"
EXISTING_REF = "public.%s" % EXISTING_TABLE
NEW_REF = "public.%s" % NEW_TABLE
NODES = ["mmr:mmr1", "mmr:mmr2", "mmr:mmr3"]


def all_nodes_command(sql, tag):
    return (
        "WITH r AS (SELECT node_name,success,result FROM fdd.run_on_all_nodes('%s')) "
        "SELECT count(*)::text,bool_and(success)::text,bool_and(result='%s')::text FROM r" %
        (sql, tag))


def mapping_step(title, set_name, relation, expected_copy_data,
                 continue_on_failure=False):
    return sql_step(
        title, "postgres",
        "SELECT count(*)::text,bool_and(set_sync_state='w')::text,"
        "bool_and(set_iscopydata)::text "
        "FROM fdd.mmr_replication_set_table t JOIN fdd.mmr_replication_set s "
        "ON s.set_id=t.set_id WHERE s.set_name='%s' AND t.set_reloid='%s'::regclass" %
        (set_name, relation),
        "返回 1|true|%s" % expected_copy_data,
        rows_equal([["1", "true", expected_copy_data]]), node="mmr:mmr1",
        continue_on_failure=continue_on_failure)


CASE = {
    "id": "mmr.replication_set.auto_table_binding",
    "name": "复制集自动绑定已有表和新建表",
    "document": "多活功能测试文档.md",
    "section": "3.5 测试一,3.5 测试二",
    "group": "replication_set",
    "fixtures": [
        "cluster",
        {"type": "mmr_replication_sets_empty", "node": "mmr:mmr1",
         "names": [EXISTING_SET, NEW_SET]},
        {"type": "mmr_remote_sql_tables", "nodes": NODES,
         "tables": [EXISTING_TABLE, NEW_TABLE]},
        {"type": "mmr_async_set_mode_recovery", "nodes": NODES},
    ],
    "requirements": {
        "plugins": ["fdd_mmr"], "groups": ["mmr"],
        "writable_node": True, "node": "mmr:mmr1",
    },
    "evidence_nodes": NODES,
    "prerequisites": [
        "三成员均 ACTIVE，两个 run_id 复制集和测试表不存在。",
        "测试复制集固定 copy_data=false，避免当前 two_phase=true 环境拒绝存量复制；自动绑定状态仍按文档校验为 w。",
    ],
    "steps": [
        sql_step(
            "在所有成员创建已有表", "postgres",
            all_nodes_command("CREATE TABLE %s(id int PRIMARY KEY)" % EXISTING_REF,
                              "CREATE TABLE"),
            "返回 3|true|true", rows_equal([["3", "true", "true"]]), node="mmr:mmr1"),
        sql_step(
            "按文档创建 autoadd_existing=true 的全局复制集", "postgres",
            "SELECT fdd.create_replication_set('%s',true,true,true,true,false,true,false,false,true)" % EXISTING_SET,
            "SQL 执行成功", command_succeeds(), node="mmr:mmr1"),
        sql_step(
            "确认已有表已自动绑定且复制集属性正确", "postgres",
            "SELECT set_autoadd_tables::text,set_iscopydata_default::text,set_isglobal::text "
            "FROM fdd.mmr_replication_set WHERE set_name='%s'" % EXISTING_SET,
            "返回 false|false|true", rows_equal([["false", "false", "true"]]), node="mmr:mmr1"),
        mapping_step("确认已有表自动绑定为等待状态", EXISTING_SET, EXISTING_REF,
                     "false", continue_on_failure=True),
        sql_step(
            "按文档创建 autoadd_tables=true 的全局复制集", "postgres",
            "SELECT fdd.create_replication_set('%s',true,true,true,true,true,false,false,false,true)" % NEW_SET,
            "SQL 执行成功", command_succeeds(), node="mmr:mmr1"),
        sql_step(
            "确认新表自动绑定复制集属性正确", "postgres",
            "SELECT set_autoadd_tables::text,set_iscopydata_default::text,set_isglobal::text "
            "FROM fdd.mmr_replication_set WHERE set_name='%s'" % NEW_SET,
            "返回 true|false|true", rows_equal([["true", "false", "true"]]), node="mmr:mmr1"),
        sql_step(
            "在所有成员创建新表触发 autoadd_tables", "postgres",
            all_nodes_command("CREATE TABLE %s(id int PRIMARY KEY,name name)" % NEW_REF,
                              "CREATE TABLE"),
            "返回 3|true|true", rows_equal([["3", "true", "true"]]), node="mmr:mmr1"),
        mapping_step("确认新建表自动绑定为等待状态", NEW_SET, NEW_REF, "false"),
        sql_step(
            "确认 autoadd_existing=false 的第二复制集未回溯绑定已有表", "postgres",
            "SELECT count(*)::text FROM fdd.mmr_replication_set_table t "
            "JOIN fdd.mmr_replication_set s ON s.set_id=t.set_id "
            "WHERE s.set_name='%s' AND t.set_reloid='%s'::regclass" % (NEW_SET, EXISTING_REF),
            "返回 0", rows_equal([["0"]]), node="mmr:mmr1"),
    ],
    "teardown": "fixture 先删除所有成员测试表，再删除两个全局复制集，最后调用 fdd.check_async_record() 恢复 set_mode。",
}
