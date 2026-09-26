from framework.assertions import command_succeeds, rows_equal
from framework.steps import sql_step


SET_NAME = "fbase_r_mmr_rs_{run_id}"
NODES = ["mmr:mmr1", "mmr:mmr2", "mmr:mmr3"]


CASE = {
    "id": "mmr.replication_set.subscription_sets",
    "name": "节点订阅复制集设置",
    "document": "多活功能测试文档.md",
    "section": "3.4",
    "group": "replication_set",
    "fixtures": [
        "cluster",
        {"type": "mmr_replication_sets_empty", "node": "mmr:mmr1", "names": [SET_NAME]},
        {"type": "mmr_sub_repsets_guard", "node": "mmr:mmr1"},
        {"type": "mmr_async_set_mode_recovery", "nodes": NODES},
    ],
    "requirements": {
        "plugins": ["fdd_mmr"], "groups": ["mmr"],
        "writable_node": True, "node": "mmr:mmr1",
    },
    "evidence_nodes": ["mmr:mmr1"],
    "prerequisites": [
        "三成员均 ACTIVE，mmr1 的原 sub_repsets 非空。",
        "用例创建 copy_data=false 的全局复制集，避免当前 two_phase=true 的存量复制限制。",
    ],
    "steps": [
        sql_step(
            "创建供订阅设置使用的全局复制集", "postgres",
            "SELECT fdd.create_replication_set('%s',true,true,true,true,false,false,false,false,true)" % SET_NAME,
            "SQL 执行成功", command_succeeds(), node="mmr:mmr1"),
        sql_step(
            "确认全局复制集已同步到三个成员", "postgres",
            "WITH r AS (SELECT node_name,success,result FROM fdd.run_on_all_nodes("
            "'SELECT count(*)::text FROM fdd.mmr_replication_set WHERE set_name=''%s''')) "
            "SELECT count(*)::text,bool_and(success)::text,bool_and(result='1')::text FROM r" % SET_NAME,
            "返回 3|true|true", rows_equal([["3", "true", "true"]]), node="mmr:mmr1"),
        sql_step(
            "按文档查看修改前本地订阅复制集", "postgres",
            "SELECT sub_repsets::text FROM fdd.mmr_local_node",
            "返回非空的当前订阅复制集数组", command_succeeds(), node="mmr:mmr1"),
        sql_step(
            "按文档将本节点订阅复制集改为测试复制集", "postgres",
            "SELECT fdd.alter_node_replication_sets(ARRAY['%s']::text[])" % SET_NAME,
            "SQL 执行成功", command_succeeds(), node="mmr:mmr1"),
        sql_step(
            "确认 mmr_local_node.sub_repsets 已更新", "postgres",
            "SELECT sub_repsets::text,set_mode::text FROM fdd.mmr_local_node",
            "返回 {%s}|n，表示新订阅复制集已登记并等待异步处理" % SET_NAME,
            rows_equal([["{%s}" % SET_NAME, "n"]]), node="mmr:mmr1"),
    ],
    "teardown": "fixture 先调用 fdd.alter_node_replication_sets() 恢复原订阅数组，再删除测试复制集；最后调用 fdd.check_async_record() 收敛 set_mode。",
}
