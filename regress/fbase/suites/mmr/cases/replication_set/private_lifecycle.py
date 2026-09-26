from framework.assertions import command_succeeds, rows_equal
from framework.steps import sql_step


SET_NAME = "fbase_r_mmr_rp_{run_id}"


CASE = {
    "id": "mmr.replication_set.private_lifecycle",
    "name": "多活私有复制集修改及删除",
    "document": "多活功能测试文档.md",
    "section": "3.2,3.3",
    "group": "replication_set",
    "fixtures": ["cluster", {"type": "mmr_replication_sets_empty", "node": "mmr:mmr1", "names": [SET_NAME]}],
    "requirements": {"plugins": ["fdd_mmr"], "groups": ["mmr"], "writable_node": True, "node": "mmr:mmr1"},
    "evidence_nodes": ["mmr:mmr1", "mmr:mmr2"],
    "prerequisites": ["三成员为 ACTIVE，run_id 私有复制集名称不存在。"],
    "steps": [
        sql_step("按文档创建私有复制集", "postgres",
                 "SELECT fdd.create_replication_set('%s',true,true,true,true,false,true,true,false,false)" % SET_NAME,
                 "SQL 执行成功", command_succeeds(), node="mmr:mmr1"),
        sql_step("确认 mmr1 私有复制集初始属性", "postgres",
                 "SELECT count(*)::text,bool_and(replicate_inserts)::text,bool_and(replicate_updates)::text,bool_and(NOT set_isglobal)::text FROM fdd.mmr_replication_set WHERE set_name='%s'" % SET_NAME,
                 "返回 1|true|true|true", rows_equal([["1", "true", "true", "true"]]), node="mmr:mmr1"),
        sql_step("确认 mmr2 未同步私有复制集", "postgres",
                 "SELECT count(*)::text FROM fdd.mmr_replication_set WHERE set_name='%s'" % SET_NAME,
                 "返回 0", rows_equal([["0"]]), node="mmr:mmr2"),
        sql_step("按文档关闭私有复制集的 insert 和 update", "postgres",
                 "SELECT fdd.alter_replication_set('%s', false, false)" % SET_NAME,
                 "SQL 执行成功", command_succeeds(), node="mmr:mmr1"),
        sql_step("确认私有复制集属性已修改", "postgres",
                 "SELECT replicate_inserts::text,replicate_updates::text FROM fdd.mmr_replication_set WHERE set_name='%s'" % SET_NAME,
                 "返回 false|false", rows_equal([["false", "false"]]), node="mmr:mmr1"),
        sql_step("按文档删除私有复制集", "postgres",
                 "SELECT fdd.drop_replication_set('%s')" % SET_NAME,
                 "SQL 执行成功", command_succeeds(), node="mmr:mmr1"),
        sql_step("确认私有复制集和映射均已删除", "postgres",
                 "SELECT count(*)::text FROM fdd.mmr_replication_set WHERE set_name='%s'" % SET_NAME,
                 "返回 0", rows_equal([["0"]]), node="mmr:mmr1"),
    ],
    "teardown": "显式删除私有复制集；fixture 在失败路径调用产品 UDF 删除残留对象。",
}
