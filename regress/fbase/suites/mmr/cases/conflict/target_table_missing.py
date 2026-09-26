from suites.mmr.cases.node_management.create_group import (PSQL, create_node,
                                                            init_instance, psql, shell)
from suites.mmr.cases.conflict.delete_missing import query, setup, sql


ROOT = "/tmp/fbase_regress_mmr_conflict_missing_table_{run_id}"
SOURCE, TARGET = ROOT + "/source", ROOT + "/target"
SOURCE_PORT, TARGET_PORT = "15491", "15492"
DSN = "host=127.0.0.1 port=%s user=postgres dbname=postgres" % SOURCE_PORT
TABLE = "drop_table_{run_id}"


CASE = {
    "id": "mmr.conflict.target_table_missing", "name": "target_table_missing 默认跳过处理",
    "document": "多活功能测试文档.md", "section": "4.1.7", "group": "conflict",
    "fixtures": ["cluster", {"type": "isolated_mmr_node_creation", "data_dir": ROOT}],
    "requirements": {"clusters": ["mmr"], "plugins": ["fdd_mmr"], "writable_node": True, "node": "mmr:mmr1"},
    "evidence_nodes": ["mmr:mmr1"],
    "prerequisites": ["两个临时节点订阅默认复制集 g1；测试表先在两端创建，再仅从 node134 删除。"],
    "steps": [
        setup(init_instance("初始化 node134", SOURCE, SOURCE_PORT)), setup(create_node(SOURCE_PORT, "node134")),
        setup(shell("创建建组探针表", psql(SOURCE_PORT, "CREATE TABLE join_probe(id int PRIMARY KEY)"), "返回 CREATE TABLE")),
        setup(shell("创建 g1 集群", psql(SOURCE_PORT, "SELECT fdd.create_group('g1')"), "返回 node group create successful")),
        setup(init_instance("初始化 node135", TARGET, TARGET_PORT)), setup(create_node(TARGET_PORT, "node135")),
        setup(shell("以 schema-only 加入 g1", psql(TARGET_PORT, "SELECT fdd.join_group('g1','%s',true,'schema-only','table_exist_error')" % DSN), "返回 node join to group complete finished", timeout=90)),
        setup(shell("按文档将所有节点订阅集设为默认 g1", psql(SOURCE_PORT, "SELECT fdd.run_on_all_nodes('SELECT fdd.alter_node_replication_sets(''{g1}'')')"), "默认复制集设置成功")),
        setup(shell("在所有节点创建测试表并完成异步设置", psql(SOURCE_PORT, "SELECT fdd.run_on_all_nodes('CREATE TABLE public.%s(id int PRIMARY KEY)'); SELECT fdd.replication_set_async_execute()" % TABLE), "测试表创建成功", timeout=90)),
        query("读取发布端实际 debug_logical_replication_streaming", SOURCE_PORT, "SHOW debug_logical_replication_streaming", "返回 buffered", "buffered"),
        query("读取订阅端实际 debug_logical_replication_streaming", TARGET_PORT, "SHOW debug_logical_replication_streaming", "返回 buffered", "buffered"),
        query("展示删除前 node134 的测试表", SOURCE_PORT, "SELECT to_regclass('public.%s') AS table_name" % TABLE, "返回测试表名", TABLE),
        query("展示删除前 node135 的测试表", TARGET_PORT, "SELECT to_regclass('public.%s') AS table_name" % TABLE, "返回测试表名", TABLE),
        query("确认触发前没有旧的 target_table_missing 记录", SOURCE_PORT, "SELECT count(*) AS target_table_missing_count FROM fdd.mmr_conflict_history WHERE conflict_type='target_table_missing'", "返回 0", "0"),
        sql("按文档仅在 node134 删除测试表", psql(SOURCE_PORT, "DROP TABLE public.%s" % TABLE), "返回 DROP TABLE", "DROP TABLE public.%s" % TABLE),
        query("确认 node134 已不存在测试表", SOURCE_PORT, "SELECT (to_regclass('public.%s') IS NULL)::text AS table_missing" % TABLE, "返回 true", "true"),
        query("确认 node135 仍存在测试表", TARGET_PORT, "SELECT to_regclass('public.%s') AS table_name" % TABLE, "返回测试表名", TABLE),
        sql("按文档在 node135 插入数据", psql(TARGET_PORT, "INSERT INTO public.%s VALUES(1)" % TABLE), "返回 INSERT 0 1", "INSERT INTO public.%s VALUES(1)" % TABLE),
        sql("等待 node134 记录 target_table_missing", "for i in $(seq 1 30); do v=$(%s -X -At -h 127.0.0.1 -p %s -U postgres -d postgres -c %r); test \"$v\" = 1 && { echo \"$v\"; exit 0; }; sleep 1; done; exit 1" % (PSQL,SOURCE_PORT,"SELECT count(*) FROM fdd.mmr_conflict_history WHERE conflict_type='target_table_missing'"), "30 秒内存在一条 target_table_missing", "SELECT count(*) FROM fdd.mmr_conflict_history WHERE conflict_type='target_table_missing'",35),
        query("展示 target_table_missing 冲突记录", SOURCE_PORT, "SELECT conflict_type,conflict_resolution,relname,apply_tuple FROM fdd.mmr_conflict_history WHERE conflict_type='target_table_missing' ORDER BY local_lsn DESC LIMIT 1", "返回 target_table_missing、skip_if_recently_dropped 和 none", "target_table_missing", "skip_if_recently_dropped", TABLE, "none"),
        query("确认 skip 后 node135 本地数据仍存在", TARGET_PORT, "SELECT id FROM public.%s ORDER BY id" % TABLE, "返回 id=1", "1"),
    ],
    "teardown": "以 immediate 停止临时 node134/node135 PostgreSQL 实例；递归删除临时数据目录，连同 join_probe、测试表、MMR 元数据、订阅、复制槽和本轮冲突记录一并删除。",
}
