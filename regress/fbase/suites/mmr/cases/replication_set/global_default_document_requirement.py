from framework.assertions import output_contains_text
from suites.mmr.cases.conflict.delete_missing import query, setup
from suites.mmr.cases.node_management.create_group import create_node, init_instance, psql, shell


ROOT = "/tmp/fbase_regress_mmr_default_global_repset_{run_id}"
SOURCE, JOINER = ROOT + "/source", ROOT + "/joiner"
SOURCE_PORT, JOINER_PORT = "15671", "15672"
SET_NAME = "fbase_r_mmr_rg_{run_id}"
TABLE = "fbase_r_mmr_rg_probe_{run_id}"
DSN = "host=127.0.0.1 port=%s user=postgres dbname=postgres" % SOURCE_PORT


CASE = {
    "id": "mmr.replication_set.global_default_document_requirement",
    "name": "默认全局复制集创建及已有表自动绑定",
    "document": "多活功能测试文档.md", "section": "3.1", "group": "replication_set",
    "fixtures": ["cluster", {"type": "isolated_mmr_node_creation", "data_dir": ROOT}],
    "requirements": {"clusters": ["mmr"], "plugins": ["fdd_mmr"],
                     "writable_node": True, "node": "mmr:mmr1"},
    "evidence_nodes": ["mmr:mmr1"],
    "prerequisites": [
        "技术文档限制：存在 two_phase=true 节点时，不能创建 set_iscopydata_default=true 的公有复制集。",
        "两个隔离成员均以 two_phase=false 创建；源端在建组前创建一张带主键的已有表。",
    ],
    "steps": [
        setup(init_instance("初始化 node134", SOURCE, SOURCE_PORT)),
        setup(create_node(SOURCE_PORT, "node134", two_phase=False)),
        setup(shell("在 node134 创建已有复制测试表", psql(SOURCE_PORT,
                    "CREATE TABLE %s(id int PRIMARY KEY)" % TABLE), "返回 CREATE TABLE")),
        setup(shell("创建 g1 集群", psql(SOURCE_PORT, "SELECT fdd.create_group('g1')"),
                    "返回 node group create successful")),
        setup(init_instance("初始化 node135", JOINER, JOINER_PORT)),
        setup(create_node(JOINER_PORT, "node135", two_phase=False)),
        setup(shell("以 all 模式将 node135 加入 g1", psql(JOINER_PORT,
                    "SELECT fdd.join_group('g1','%s',true,'all','table_exist_error')" % DSN),
                    "返回 node join to group complete finished", timeout=90)),
        query("确认两个成员的 two_phase 均为 false", SOURCE_PORT,
              "SELECT node_name,two_phase FROM fdd.mmr_node ORDER BY node_name",
              "返回 node134|f 和 node135|f", "node134", "f", "node135"),
        shell("按文档默认参数创建全局复制集", psql(SOURCE_PORT,
              "SELECT fdd.create_replication_set('%s')" % SET_NAME),
              "返回 replication set create successful"),
        query("确认全局复制集同步到两个成员", SOURCE_PORT,
              "SELECT count(*)::text,bool_and(set_isglobal)::text,bool_and(set_iscopydata_default)::text "
              "FROM fdd.mmr_replication_set WHERE set_name='%s'" % SET_NAME,
              "返回 1|true|true", "1", "true"),
        query("确认默认 autoadd_existing 已绑定源端已有表", SOURCE_PORT,
              "SELECT count(*)::text FROM fdd.mmr_replication_set_table t "
              "JOIN fdd.mmr_replication_set s ON s.set_id=t.set_id "
              "WHERE s.set_name='%s' AND t.set_reloid='%s'::regclass" % (SET_NAME, TABLE),
              "返回 1", "1"),
        query("确认 node135 同步看到全局复制集", JOINER_PORT,
              "SELECT count(*)::text FROM fdd.mmr_replication_set WHERE set_name='%s'" % SET_NAME,
              "返回 1", "1"),
    ],
    "teardown": "fixture 以 immediate 停止临时 node134/node135 并删除整个临时目录，移除测试表、复制集、订阅、复制槽及元数据。",
}
