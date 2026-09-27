from framework.assertions import command_succeeds
from suites.mmr.cases.conflict.delete_missing import query, setup
from suites.mmr.cases.node_management.create_group import create_node, init_instance, psql, shell


ROOT = "/tmp/fbase_regress_mmr_replication_set_removal_{run_id}"
SOURCE, TARGET = ROOT + "/node134", ROOT + "/node135"
SOURCE_PORT, TARGET_PORT = "15675", "15676"
DSN = "host=127.0.0.1 port=%s user=postgres dbname=postgres" % SOURCE_PORT
SET_NAME = "fbase_r_mmr_rsr_{run_id}"
TABLE = "fbase_r_mmr_rsr_t_{run_id}"
TABLE_REF = "public.%s" % TABLE
BOOTSTRAP = "fbase_r_mmr_rsr_bootstrap_{run_id}"


CASE = {
    "id": "mmr.replication_set.synchronous_removal",
    "name": "复制集异步执行后同步移除表",
    "document": "多活功能测试文档.md", "section": "3.6 测试二,3.7",
    "group": "replication_set",
    "fixtures": ["cluster", {"type": "isolated_mmr_node_creation", "data_dir": ROOT}],
    "requirements": {"plugins": ["fdd_mmr"], "writable_node": True,
                     "node": "mmr:mmr1"},
    "evidence_nodes": ["mmr:mmr1"],
    "prerequisites": [
        "两个隔离节点均为 ACTIVE 且 two_phase=false；不使用共享三节点 two_phase 集群。",
        "测试复制集、测试表、订阅和复制槽只存在于本轮临时数据目录。",
    ],
    "steps": [
        setup(init_instance("初始化 node134", SOURCE, SOURCE_PORT)),
        setup(shell("在 node134 创建 schema-only join 所需引导表", psql(SOURCE_PORT,
                    "CREATE TABLE public.%s(id int PRIMARY KEY)" % BOOTSTRAP),
                    "返回 CREATE TABLE")),
        setup(create_node(SOURCE_PORT, "node134", two_phase=False)),
        setup(shell("创建隔离 g1 集群", psql(SOURCE_PORT,
                    "SELECT fdd.create_group('g1')"), "返回 node group create successful")),
        setup(init_instance("初始化 node135", TARGET, TARGET_PORT)),
        setup(shell("在 node135 创建同构引导表", psql(TARGET_PORT,
                    "CREATE TABLE public.%s(id int PRIMARY KEY)" % BOOTSTRAP),
                    "返回 CREATE TABLE")),
        setup(create_node(TARGET_PORT, "node135", two_phase=False)),
        setup(shell("以 schema-only 将 node135 加入 g1", psql(TARGET_PORT,
                    "SELECT fdd.join_group('g1','%s',true,'schema-only','table_exist_error')" % DSN),
                    "返回 node join to group complete finished", command_succeeds(), timeout=90)),
        query("确认两个成员均关闭 two_phase", SOURCE_PORT,
              "SELECT string_agg(node_name || '|' || two_phase::text, ';' ORDER BY node_name) FROM fdd.mmr_node",
              "返回 node134|false；node135|false", "node134|false;node135|false"),
        shell("创建不自动绑定的全局复制集", psql(SOURCE_PORT,
              "SELECT fdd.create_replication_set('%s',true,true,true,true,false,false,false,false,true)" % SET_NAME),
              "SQL 执行成功"),
        shell("在所有成员创建同构测试表", psql(SOURCE_PORT,
              "WITH r AS (SELECT node_name,success,result FROM fdd.run_on_all_nodes('CREATE TABLE %s(id int PRIMARY KEY)')) "
              "SELECT count(*)::text,bool_and(success)::text,bool_and(result='CREATE TABLE')::text FROM r" % TABLE_REF),
              "返回 2|true|true"),
        shell("异步添加测试表且不复制存量", psql(SOURCE_PORT,
              "SELECT fdd.replication_set_add_table('%s'::regclass,'%s',false,false)" % (TABLE_REF, SET_NAME)),
              "SQL 执行成功"),
        query("确认异步添加记录为 w", SOURCE_PORT,
              "SELECT set_sync_state::text,set_iscopydata::text FROM fdd.mmr_replication_set_table t JOIN fdd.mmr_replication_set s "
              "ON s.set_id=t.set_id WHERE s.set_name='%s' AND t.set_reloid='%s'::regclass" % (SET_NAME, TABLE_REF),
              "返回 w|false", "w", "f"),
        shell("设置本节点订阅测试复制集", psql(SOURCE_PORT,
              "SELECT fdd.alter_node_replication_sets(ARRAY['%s']::text[])" % SET_NAME), "SQL 执行成功"),
        query("确认订阅设置使 set_mode 进入 n", SOURCE_PORT,
              "SELECT sub_repsets::text,set_mode::text FROM fdd.mmr_local_node",
              "返回 {%s}|n" % SET_NAME, "{%s}" % SET_NAME, "n"),
        shell("按文档执行异步复制集处理", psql(SOURCE_PORT,
              "SELECT fdd.replication_set_async_execute(true)"), "SQL 执行成功", command_succeeds(), timeout=60),
        query("确认异步处理后表为 s 且 set_mode 已完成", SOURCE_PORT,
              "SELECT (SELECT set_sync_state::text FROM fdd.mmr_replication_set_table t JOIN fdd.mmr_replication_set s "
              "ON s.set_id=t.set_id WHERE s.set_name='%s' AND t.set_reloid='%s'::regclass),(SELECT set_mode::text FROM fdd.mmr_local_node)" % (SET_NAME, TABLE_REF),
              "返回 s|d；产品源码将异步阶段完成状态置为 d", "s", "d"),
        shell("按文档同步移除已订阅复制集中的表", psql(SOURCE_PORT,
              "SELECT fdd.replication_set_remove_table('%s'::regclass,'%s',true)" % (TABLE_REF, SET_NAME)),
              "SQL 执行成功", command_succeeds(), timeout=60),
        query("确认同步移除后映射记录已删除", SOURCE_PORT,
              "SELECT count(*)::text FROM fdd.mmr_replication_set_table t JOIN fdd.mmr_replication_set s "
              "ON s.set_id=t.set_id WHERE s.set_name='%s' AND t.set_reloid='%s'::regclass" % (SET_NAME, TABLE_REF),
              "返回 0", "0"),
    ],
    "teardown": "fixture 以 immediate 停止 node134/node135 并删除临时目录；其中包含测试表、复制集、订阅、复制槽和异步状态。",
}
