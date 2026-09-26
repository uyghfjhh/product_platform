from framework.assertions import rows_equal
from framework.steps import sql_step


SINGLE_TABLE = "fbase_regress_mmr_remote_single_{run_id}"
ALL_TABLE = "fbase_regress_mmr_remote_all_{run_id}"
SINGLE_REF = "public.%s" % SINGLE_TABLE
ALL_REF = "public.%s" % ALL_TABLE


def _command_summary(function, command, expected_tag, nodes=None):
    if nodes is None:
        invocation = "fdd.%s('%s')" % (function, command)
    else:
        invocation = "fdd.%s('%s', true, ARRAY[%s])" % (
            function, command, ",".join("'%s'" % name for name in nodes))
    return (
        "WITH command_result AS (SELECT node_name,success,result FROM %s) "
        "SELECT count(*)::text,bool_and(success)::text,"
        "bool_and(result = '%s')::text FROM command_result" %
        (invocation, expected_tag))


CASE = {
    "id": "mmr.remote_sql.command_and_all_nodes",
    "name": "多活指定节点及全节点远程 SQL 执行",
    "document": "多活功能测试文档.md",
    "section": "9.1",
    "group": "remote_sql",
    "fixtures": [
        "cluster",
        {"type": "mmr_remote_sql_tables",
         "nodes": ["mmr:mmr1", "mmr:mmr2", "mmr:mmr3"],
         "tables": [SINGLE_TABLE, ALL_TABLE]},
        {"type": "mmr_async_set_mode_recovery",
         "nodes": ["mmr:mmr1", "mmr:mmr2", "mmr:mmr3"]},
    ],
    "requirements": {
        "plugins": ["fdd_mmr"], "groups": ["mmr"],
        "writable_node": True, "node": "mmr:mmr1",
    },
    "evidence_nodes": ["mmr:mmr1", "mmr:mmr2", "mmr:mmr3"],
    "prerequisites": [
        "三个成员均为 ACTIVE，mmr1 可通过 fdd.run_command_on_nodes 连接 mmr2。",
        "三个成员均不存在本 run_id 对应的两张测试表；fixture 会在任何结束路径删除它们。",
    ],
    "steps": [
        sql_step(
            "按文档在指定成员 mmr2 创建单节点测试表", "postgres",
            _command_summary(
                "run_command_on_nodes",
                "CREATE TABLE %s(id int PRIMARY KEY)" % SINGLE_REF,
                "CREATE TABLE", ["mmr2"]),
            "返回 1|true|true：mmr2 返回 CREATE TABLE",
            rows_equal([["1", "true", "true"]]), node="mmr:mmr1"),
        sql_step(
            "按文档向指定成员 mmr2 插入一行", "postgres",
            _command_summary(
                "run_command_on_nodes", "INSERT INTO %s VALUES(1)" % SINGLE_REF,
                "INSERT 0 1", ["mmr2"]),
            "返回 1|true|true：mmr2 返回 INSERT 0 1",
            rows_equal([["1", "true", "true"]]), node="mmr:mmr1"),
        sql_step(
            "在 mmr2 查询指定节点插入结果", "postgres",
            "SELECT id::text FROM %s" % SINGLE_REF,
            "返回 1", rows_equal([["1"]]), node="mmr:mmr2"),
        sql_step(
            "按文档从指定成员 mmr2 删除单节点测试表", "postgres",
            _command_summary(
                "run_command_on_nodes", "DROP TABLE %s" % SINGLE_REF,
                "DROP TABLE", ["mmr2"]),
            "返回 1|true|true：mmr2 返回 DROP TABLE",
            rows_equal([["1", "true", "true"]]), node="mmr:mmr1"),
        sql_step(
            "确认 mmr2 的单节点测试表已删除", "postgres",
            "SELECT (to_regclass('%s') IS NULL)::text" % SINGLE_REF,
            "返回 true", rows_equal([["true"]]), node="mmr:mmr2"),
        sql_step(
            "按文档在所有多活成员创建同名测试表", "postgres",
            _command_summary(
                "run_on_all_nodes", "CREATE TABLE %s(id int PRIMARY KEY)" % ALL_REF,
                "CREATE TABLE"),
            "返回 3|true|true：三个成员均返回 CREATE TABLE",
            rows_equal([["3", "true", "true"]]), node="mmr:mmr1"),
        sql_step(
            "按文档在所有多活成员插入一行", "postgres",
            _command_summary("run_on_all_nodes", "INSERT INTO %s VALUES(1)" % ALL_REF,
                             "INSERT 0 1"),
            "返回 3|true|true：三个成员均返回 INSERT 0 1",
            rows_equal([["3", "true", "true"]]), node="mmr:mmr1"),
        sql_step(
            "在 mmr1 验证全节点远程插入结果", "postgres",
            "SELECT id::text FROM %s" % ALL_REF,
            "返回 1", rows_equal([["1"]]), node="mmr:mmr1"),
        sql_step(
            "在 mmr2 验证全节点远程插入结果", "postgres",
            "SELECT id::text FROM %s" % ALL_REF,
            "返回 1", rows_equal([["1"]]), node="mmr:mmr2"),
        sql_step(
            "在 mmr3 验证全节点远程插入结果", "postgres",
            "SELECT id::text FROM %s" % ALL_REF,
            "返回 1", rows_equal([["1"]]), node="mmr:mmr3"),
        sql_step(
            "在所有多活成员删除全节点测试表", "postgres",
            _command_summary("run_on_all_nodes", "DROP TABLE %s" % ALL_REF,
                             "DROP TABLE"),
            "返回 3|true|true：三个成员均返回 DROP TABLE",
            rows_equal([["3", "true", "true"]]), node="mmr:mmr1"),
    ],
    "teardown": "显式删除两张测试表；fixture 在失败路径删除表后调用 fdd.check_async_record() 恢复所有成员 set_mode。",
}
