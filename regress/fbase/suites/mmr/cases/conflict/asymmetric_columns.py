from suites.mmr.cases.node_management.create_group import (PSQL, create_node,
                                                            init_instance, psql, shell)
from suites.mmr.cases.conflict.delete_missing import query, setup, sql


ROOT = "/tmp/fbase_regress_mmr_conflict_columns_{run_id}"
SOURCE, TARGET = ROOT + "/source", ROOT + "/target"
SOURCE_PORT, TARGET_PORT = "15489", "15490"
DSN = "host=127.0.0.1 port=%s user=postgres dbname=postgres" % SOURCE_PORT
TABLE = "lack_column_{run_id}"
SET = "set_column_{run_id}"


CASE = {
    "id": "mmr.conflict.asymmetric_columns", "name": "异构列双向冲突的默认处理",
    "document": "多活功能测试文档.md", "section": "4.1.5,4.1.6", "group": "conflict",
    "fixtures": ["cluster", {"type": "isolated_mmr_node_creation", "data_dir": ROOT}],
    "requirements": {"clusters": ["mmr"], "plugins": ["fdd_mmr"], "writable_node": True, "node": "mmr:mmr1"},
    "evidence_nodes": ["mmr:mmr1"],
    "prerequisites": ["临时 node134 表包含 city 默认列，node135 缺少 city；私有复制集仅复制本用例表。"],
    "steps": [
        setup(init_instance("初始化源节点 node134", SOURCE, SOURCE_PORT)), setup(create_node(SOURCE_PORT, "node134")),
        setup(shell("创建建组探针表", psql(SOURCE_PORT, "CREATE TABLE join_probe(id int PRIMARY KEY)"), "返回 CREATE TABLE")),
        setup(shell("创建 g1 集群", psql(SOURCE_PORT, "SELECT fdd.create_group('g1')"), "返回 node group create successful")),
        setup(init_instance("初始化节点 node135", TARGET, TARGET_PORT)), setup(create_node(TARGET_PORT, "node135")),
        setup(shell("以 schema-only 加入 g1", psql(TARGET_PORT, "SELECT fdd.join_group('g1','%s',true,'schema-only','table_exist_error')" % DSN), "返回 node join to group complete finished", timeout=90)),
        setup(shell("源端创建带 city 默认列的测试表", psql(SOURCE_PORT, "CREATE TABLE public.%s(id int PRIMARY KEY,name name,city text DEFAULT 'changsha')" % TABLE), "返回 CREATE TABLE")),
        setup(shell("目标端创建缺少 city 的测试表", psql(TARGET_PORT, "CREATE TABLE public.%s(id int PRIMARY KEY,name name)" % TABLE), "返回 CREATE TABLE")),
        setup(shell("创建私有复制集并异步绑定异构表", psql(SOURCE_PORT, "SELECT fdd.create_replication_set('%s',true,true,true,true,false,false,false,false,true)" % SET) + "; " + psql(SOURCE_PORT, "SELECT fdd.alter_mmr_check_node_conf(ARRAY['public.%s'])" % TABLE) + "; " + psql(SOURCE_PORT, "SELECT fdd.replication_set_add_table('public.%s'::regclass,'%s',false,false)" % (TABLE,SET)) + "; " + psql(SOURCE_PORT, "SELECT fdd.run_on_all_nodes('SELECT fdd.alter_node_replication_sets(''{%s}'')')" % SET) + "; " + psql(SOURCE_PORT, "SELECT fdd.replication_set_async_execute()"), "私有复制集绑定成功", timeout=90)),
        query("读取发布端实际 debug_logical_replication_streaming", SOURCE_PORT, "SHOW debug_logical_replication_streaming", "返回 buffered", "buffered"),
        query("读取订阅端实际 debug_logical_replication_streaming", TARGET_PORT, "SHOW debug_logical_replication_streaming", "返回 buffered", "buffered"),
        query("展示 node134 的表列定义", SOURCE_PORT, "SELECT column_name,data_type,column_default FROM information_schema.columns WHERE table_schema='public' AND table_name='%s' ORDER BY ordinal_position" % TABLE, "包含 id、name、city 和 changsha 默认值", "id", "name", "city", "changsha"),
        query("展示 node135 的表列定义", TARGET_PORT, "SELECT column_name,data_type FROM information_schema.columns WHERE table_schema='public' AND table_name='%s' ORDER BY ordinal_position" % TABLE, "仅包含 id、name", "id", "name"),
        query("确认触发前没有旧的 target_column_missing 记录", TARGET_PORT, "SELECT count(*) AS target_column_missing_count FROM fdd.mmr_conflict_history WHERE conflict_type='target_column_missing'", "返回 0", "0"),
        sql("按文档在 node134 插入包含 city 的数据", psql(SOURCE_PORT, "INSERT INTO public.%s VALUES(1,'aa','shanghai')" % TABLE), "返回 INSERT 0 1", "INSERT INTO public.%s VALUES(1,'aa','shanghai')" % TABLE),
        sql("等待 node135 记录 target_column_missing", "for i in $(seq 1 30); do v=$(%s -X -At -h 127.0.0.1 -p %s -U postgres -d postgres -c %r); test \"$v\" = 1 && { echo \"$v\"; exit 0; }; sleep 1; done; exit 1" % (PSQL,TARGET_PORT,"SELECT count(*) FROM fdd.mmr_conflict_history WHERE conflict_type='target_column_missing'"), "30 秒内存在一条 target_column_missing", "SELECT count(*) FROM fdd.mmr_conflict_history WHERE conflict_type='target_column_missing'",35),
        query("确认 ignore_if_null 忽略 node135 的该行插入", TARGET_PORT, "SELECT id,name FROM public.%s ORDER BY id" % TABLE, "返回 0 行", "(0 rows)"),
        query("展示 target_column_missing 冲突记录", TARGET_PORT, "SELECT conflict_type,conflict_resolution,relname,apply_tuple FROM fdd.mmr_conflict_history WHERE conflict_type='target_column_missing' ORDER BY local_lsn DESC LIMIT 1", "返回 target_column_missing、ignore_if_null 和 none", "target_column_missing", "ignore_if_null", "none"),
        query("确认触发前没有旧的 source_column_missing 记录", SOURCE_PORT, "SELECT count(*) AS source_column_missing_count FROM fdd.mmr_conflict_history WHERE conflict_type='source_column_missing'", "返回 0", "0"),
        sql("按文档在 node135 插入缺少 city 的数据", psql(TARGET_PORT, "INSERT INTO public.%s VALUES(2,'aa')" % TABLE), "返回 INSERT 0 1", "INSERT INTO public.%s VALUES(2,'aa')" % TABLE),
        sql("等待 node134 记录 source_column_missing", "for i in $(seq 1 30); do v=$(%s -X -At -h 127.0.0.1 -p %s -U postgres -d postgres -c %r); test \"$v\" = 1 && { echo \"$v\"; exit 0; }; sleep 1; done; exit 1" % (PSQL,SOURCE_PORT,"SELECT count(*) FROM fdd.mmr_conflict_history WHERE conflict_type='source_column_missing'"), "30 秒内存在一条 source_column_missing", "SELECT count(*) FROM fdd.mmr_conflict_history WHERE conflict_type='source_column_missing'",35),
        query("确认 use_default_value 在 node134 使用 city 默认值", SOURCE_PORT, "SELECT id,name,city FROM public.%s ORDER BY id" % TABLE, "返回 1|aa|shanghai 和 2|aa|changsha", "1", "shanghai", "2", "changsha"),
        query("展示 source_column_missing 冲突记录", SOURCE_PORT, "SELECT conflict_type,conflict_resolution,relname,apply_tuple FROM fdd.mmr_conflict_history WHERE conflict_type='source_column_missing' ORDER BY local_lsn DESC LIMIT 1", "返回 source_column_missing、use_default_value 和 none", "source_column_missing", "use_default_value", "none"),
        sql("按文档补齐 node135 的 city 列", psql(TARGET_PORT, "ALTER TABLE public.%s ADD COLUMN city text DEFAULT 'changsha'" % TABLE), "返回 ALTER TABLE", "ALTER TABLE public.%s ADD COLUMN city text DEFAULT 'changsha'" % TABLE),
        query("确认 node135 最终表结构已补齐 city 列", TARGET_PORT, "SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name='%s' ORDER BY ordinal_position" % TABLE, "包含 city", "city"),
    ],
    "teardown": "以 immediate 停止临时 node134/node135 PostgreSQL 实例；递归删除临时数据目录，连同 join_probe、异构列测试表、私有复制集、MMR 元数据、订阅、复制槽和本轮冲突记录一并删除。",
}
