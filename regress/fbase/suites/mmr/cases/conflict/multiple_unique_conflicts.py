from suites.mmr.cases.node_management.create_group import (PSQL, create_node,
                                                            init_instance, psql, shell)
from suites.mmr.cases.conflict.delete_missing import query, setup, sql


ROOT = "/tmp/fbase_regress_mmr_conflict_multi_unique_{run_id}"
SOURCE, PEER, JOINER = ROOT + "/source", ROOT + "/peer", ROOT + "/joiner"
SOURCE_PORT, PEER_PORT, JOINER_PORT = "15493", "15494", "15495"
DSN = "host=127.0.0.1 port=%s user=postgres dbname=postgres" % SOURCE_PORT
TABLE = "multi_unique_{run_id}"


CASE = {
    "id": "mmr.conflict.multiple_unique_conflicts", "name": "多唯一约束冲突默认处理",
    "document": "多活功能测试文档.md", "section": "4.1.8", "group": "conflict",
    "fixtures": ["cluster", {"type": "isolated_mmr_node_creation", "data_dir": ROOT}],
    "requirements": {"clusters": ["mmr"], "plugins": ["fdd_mmr"], "writable_node": True, "node": "mmr:mmr1"},
    "evidence_nodes": ["mmr:mmr1"],
    "prerequisites": ["node134/node135 是含两行初始数据的多活成员；node136 以 schema-only 加入且无存量数据。"],
    "steps": [
        setup(init_instance("初始化 node134", SOURCE, SOURCE_PORT)), setup(create_node(SOURCE_PORT, "node134")),
        setup(shell("创建双唯一约束测试表", psql(SOURCE_PORT, "CREATE TABLE public.%s(id int PRIMARY KEY,name text,age int,city int,country int); CREATE UNIQUE INDEX %s_age_idx ON public.%s(age); CREATE UNIQUE INDEX %s_city_idx ON public.%s(city,country)" % (TABLE,TABLE,TABLE,TABLE,TABLE)), "测试表和两个唯一索引创建成功")),
        setup(shell("创建 g1 集群", psql(SOURCE_PORT, "SELECT fdd.create_group('g1')"), "返回 node group create successful")),
        setup(init_instance("初始化 node135", PEER, PEER_PORT)), setup(create_node(PEER_PORT, "node135")),
        setup(shell("以 all 模式将 node135 加入 g1", psql(PEER_PORT, "SELECT fdd.join_group('g1','%s',true,'all','table_exist_error')" % DSN), "返回 node join to group complete finished", timeout=90)),
        setup(shell("按文档在 node134 写入两行初始数据", psql(SOURCE_PORT, "INSERT INTO public.%s VALUES(1,'name',1,1,1),(2,'name',2,2,2)" % TABLE), "返回 INSERT 0 2")),
        setup(shell("等待 node135 收到两行初始数据", "for i in $(seq 1 30); do test \"$(%s -X -At -h 127.0.0.1 -p %s -U postgres -d postgres -c %r)\" = 2 && exit 0; sleep 1; done; exit 1" % (PSQL,PEER_PORT,"SELECT count(*) FROM public.%s" % TABLE), "30 秒内收到两行", timeout=35)),
        setup(init_instance("初始化 schema-only node136", JOINER, JOINER_PORT)), setup(create_node(JOINER_PORT, "node136")),
        setup(shell("按文档以 schema-only 将 node136 加入 g1", psql(JOINER_PORT, "SELECT fdd.join_group('g1','%s',true,'schema-only','table_exist_error')" % DSN), "返回 node join to group complete finished", timeout=90)),
        query("读取 node134 实际 debug_logical_replication_streaming", SOURCE_PORT, "SHOW debug_logical_replication_streaming", "返回 buffered", "buffered"),
        query("读取 node136 实际 debug_logical_replication_streaming", JOINER_PORT, "SHOW debug_logical_replication_streaming", "返回 buffered", "buffered"),
        query("展示 node134 冲突前两行初始数据", SOURCE_PORT, "SELECT id,name,age,city,country FROM public.%s ORDER BY id" % TABLE, "返回 id=1、2 两行", "1", "2"),
        query("展示 node135 冲突前两行初始数据", PEER_PORT, "SELECT id,name,age,city,country FROM public.%s ORDER BY id" % TABLE, "返回 id=1、2 两行", "1", "2"),
        query("展示 node136 schema-only 表数据", JOINER_PORT, "SELECT id,name,age,city,country FROM public.%s ORDER BY id" % TABLE, "返回 0 行", "(0 rows)"),
        query("确认 node134 触发前没有旧的 multiple_unique_conflicts", SOURCE_PORT, "SELECT count(*) AS multi_unique_count FROM fdd.mmr_conflict_history WHERE conflict_type='multiple_unique_conflicts'", "返回 0", "0"),
        sql("按文档在 node136 插入同时冲突 age 和 city/country 的数据", psql(JOINER_PORT, "INSERT INTO public.%s VALUES(4,'name',1,2,2)" % TABLE), "返回 INSERT 0 1", "INSERT INTO public.%s VALUES(4,'name',1,2,2)" % TABLE),
        sql("等待 node134 记录 multiple_unique_conflicts", "for i in $(seq 1 30); do v=$(%s -X -At -h 127.0.0.1 -p %s -U postgres -d postgres -c %r); test \"$v\" = 1 && { echo \"$v\"; exit 0; }; sleep 1; done; exit 1" % (PSQL,SOURCE_PORT,"SELECT count(*) FROM fdd.mmr_conflict_history WHERE conflict_type='multiple_unique_conflicts'"), "30 秒内存在一条 multiple_unique_conflicts", "SELECT count(*) FROM fdd.mmr_conflict_history WHERE conflict_type='multiple_unique_conflicts'",35),
        query("展示 node134 的多唯一约束冲突记录", SOURCE_PORT, "SELECT conflict_type,conflict_resolution,relname,apply_tuple FROM fdd.mmr_conflict_history WHERE conflict_type='multiple_unique_conflicts' ORDER BY local_lsn DESC LIMIT 1", "返回 multiple_unique_conflicts、error 和 none", "multiple_unique_conflicts", "error", TABLE, "none"),
        query("确认 node134 数据未因冲突改变", SOURCE_PORT, "SELECT id,name,age,city,country FROM public.%s ORDER BY id" % TABLE, "仍仅返回 id=1、2", "1", "2"),
        query("确认 node135 数据未因冲突改变", PEER_PORT, "SELECT id,name,age,city,country FROM public.%s ORDER BY id" % TABLE, "仍仅返回 id=1、2", "1", "2"),
    ],
    "teardown": "以 immediate 停止临时 node134/node135/node136 PostgreSQL 实例；递归删除临时数据目录，连同多唯一约束表、索引、MMR 元数据、订阅、复制槽和本轮冲突记录一并删除。",
}
