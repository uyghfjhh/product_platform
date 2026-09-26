from framework.assertions import command_succeeds
from suites.mmr.cases.conflict.delete_missing import query, setup
from suites.mmr.cases.node_management.create_group import create_node, init_instance, psql, shell


ROOT = "/tmp/fbase_regress_mmr_stream_prepare_{run_id}"
SOURCE, TARGET = ROOT + "/node134", ROOT + "/node135"
SOURCE_PORT, TARGET_PORT = "15673", "15674"
TABLE = "fbase_r_mmr_stream_prepare_{run_id}"
DSN = "host=127.0.0.1 port=%s user=postgres dbname=postgres" % SOURCE_PORT


def documented_async_refresh():
    step = shell("按文档异步纳入默认复制集", psql(SOURCE_PORT,
                 "SELECT fdd.replication_set_async_execute(true)"),
                 "SQL 执行成功，随后可进行 transaction streaming 数据写入")
    step["continue_on_failure"] = True
    return step


CASE = {
    "id": "mmr.streaming.default_publication_preparation",
    "name": "transaction streaming 默认发布准备",
    "document": "多活功能测试文档.md", "section": "9.4.1", "group": "streaming",
    "known_issue": "D-012",
    "fixtures": ["cluster", {"type": "isolated_mmr_node_creation", "data_dir": ROOT}],
    "requirements": {"clusters": ["mmr"], "plugins": ["fdd_mmr"],
                     "writable_node": True, "node": "mmr:mmr1"},
    "evidence_nodes": ["mmr:mmr1"],
    "prerequisites": [
        "第一阶段严格执行 9.4.1 原文：先建 stream_test，再建组，node135 以 schema-only 加入。",
        "随后按 9.4 的共同要求在所有节点创建同构表并执行 fdd.replication_set_async_execute。",
    ],
    "steps": [
        setup(init_instance("初始化 node134", SOURCE, SOURCE_PORT)),
        shell("按 9.4.1 在 node134 建组前创建 stream_test", psql(SOURCE_PORT,
              "CREATE TABLE public.stream_test(id int PRIMARY KEY,name text)"), "返回 CREATE TABLE"),
        setup(create_node(SOURCE_PORT, "node134", streaming="on", two_phase=False)),
        shell("按 9.4.1 在 node134 创建 g1 集群", psql(SOURCE_PORT,
              "SELECT fdd.create_group('g1')"), "返回 node group create successful"),
        query("确认原文建组后的默认 g1 未自动绑定建组前 stream_test", SOURCE_PORT,
              "SELECT count(*)::text FROM fdd.mmr_replication_set_table t JOIN fdd.mmr_replication_set s "
              "ON s.set_id=t.set_id WHERE s.set_name='g1' AND t.set_reloid='public.stream_test'::regclass",
              "返回 0", "0"),
        setup(init_instance("初始化 node135", TARGET, TARGET_PORT)),
        shell("按 9.4.1 在 node135 创建同构 stream_test", psql(TARGET_PORT,
              "CREATE TABLE public.stream_test(id int PRIMARY KEY,name text)"), "返回 CREATE TABLE"),
        setup(create_node(TARGET_PORT, "node135", streaming="on", two_phase=True)),
        shell("严格按 9.4.1 以 schema-only 将 node135 加入 g1", psql(TARGET_PORT,
              "SELECT fdd.join_group('g1','%s',true,'schema-only','table_exist_error')" % DSN),
              "返回 node join to group complete finished", command_succeeds(), timeout=90),
        query("确认文档要求的节点 streaming 与 two_phase 前提", SOURCE_PORT,
              "SELECT node_name,CASE streaming WHEN 't' THEN 'on' WHEN 'p' THEN 'parallel' ELSE 'off' END,two_phase "
              "FROM fdd.mmr_node ORDER BY node_name",
              "返回 node134|on|f 和 node135|on|t", "node134", "on", "f", "node135", "t"),
        query("确认默认 g1 的 copy-data 默认值为 false", SOURCE_PORT,
              "SELECT set_iscopydata_default FROM fdd.mmr_replication_set WHERE set_name='g1'",
              "返回 false", "f"),
        shell("按 9.4 共同要求在两成员同步创建待纳入默认复制集的同构 streaming 表", psql(SOURCE_PORT,
                    "SELECT count(*)::text,bool_and(success)::text,bool_and(result='CREATE TABLE')::text "
                    "FROM fdd.run_on_all_nodes('CREATE TABLE public.%s(id int PRIMARY KEY,name text)')" % TABLE),
                    "返回 2|true|true"),
        documented_async_refresh(),
        query("确认失败后默认复制集进入待恢复状态", SOURCE_PORT,
              "SELECT set_mode FROM fdd.mmr_local_node", "返回 n", "n"),
    ],
    "teardown": "fixture 以 immediate 停止临时 node134/node135 并删除整个临时目录，移除测试表、MMR 元数据、订阅、复制槽和失败的异步状态。",
}
