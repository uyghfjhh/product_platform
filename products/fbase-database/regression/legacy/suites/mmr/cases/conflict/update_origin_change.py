from suites.mmr.cases.node_management.create_group import (PSQL, create_node,
                                                            init_instance, psql, shell)
from suites.mmr.cases.conflict.delete_missing import query, setup, sql


ROOT = "/tmp/fbase_regress_mmr_conflict_origin_{run_id}"
SOURCE, PEER, OBSERVER = ROOT + "/source", ROOT + "/peer", ROOT + "/observer"
SOURCE_PORT, PEER_PORT, OBSERVER_PORT = "15498", "15499", "15500"
DSN = "host=127.0.0.1 port=%s user=postgres dbname=postgres" % SOURCE_PORT
TABLE = "origin_change_{run_id}"


CASE = {
    "id": "mmr.conflict.update_origin_change", "name": "三节点 update_origin_change 默认处理",
    "document": "多活功能测试文档.md", "section": "4.1.10", "group": "conflict",
    "fixtures": ["cluster", {"type": "isolated_mmr_node_creation", "data_dir": ROOT}],
    "requirements": {"clusters": ["mmr"], "plugins": ["fdd_mmr"], "writable_node": True, "node": "mmr:mmr1"},
    "evidence_nodes": ["mmr:mmr1"],
    "prerequisites": ["三个临时 ACTIVE 节点；node136 已完成 schema-only join，后续接收 node134 与 node135 对同一行的连续更新。"],
    "steps": [
        setup(init_instance("初始化 node134", SOURCE, SOURCE_PORT)), setup(create_node(SOURCE_PORT, "node134")),
        setup(shell("创建冲突测试表", psql(SOURCE_PORT, "CREATE TABLE public.%s(id int PRIMARY KEY,title text)" % TABLE), "返回 CREATE TABLE")),
        setup(shell("创建 g1 集群", psql(SOURCE_PORT, "SELECT fdd.create_group('g1')"), "返回 node group create successful")),
        setup(init_instance("初始化 node135", PEER, PEER_PORT)), setup(create_node(PEER_PORT, "node135")),
        setup(shell("以 all 模式将 node135 加入 g1", psql(PEER_PORT, "SELECT fdd.join_group('g1','%s',true,'all','table_exist_error')" % DSN), "返回 node join to group complete finished", timeout=90)),
        setup(init_instance("初始化 node136", OBSERVER, OBSERVER_PORT)), setup(create_node(OBSERVER_PORT, "node136")),
        setup(shell("以 schema-only 模式将 node136 加入 g1", psql(OBSERVER_PORT, "SELECT fdd.join_group('g1','%s',true,'schema-only','table_exist_error')" % DSN), "返回 node join to group complete finished", timeout=90)),
        query("读取 node134 实际 debug_logical_replication_streaming", SOURCE_PORT, "SHOW debug_logical_replication_streaming", "返回 buffered", "buffered"),
        query("读取 node136 实际 debug_logical_replication_streaming", OBSERVER_PORT, "SHOW debug_logical_replication_streaming", "返回 buffered", "buffered"),
        query("展示 node136 触发前表数据", OBSERVER_PORT, "SELECT id,title FROM public.%s ORDER BY id" % TABLE, "返回 0 行", "(0 rows)"),
        query("确认触发前 node136 没有旧的 update_origin_change", OBSERVER_PORT, "SELECT count(*) AS origin_change_count FROM fdd.mmr_conflict_history WHERE conflict_type='update_origin_change'", "返回 0", "0"),
        sql("按文档在 node134 插入 id=11", psql(SOURCE_PORT, "INSERT INTO public.%s VALUES(11,'vv')" % TABLE), "返回 INSERT 0 1", "INSERT INTO public.%s VALUES(11,'vv')" % TABLE),
        sql("按文档在 node134 先更新为 pp", psql(SOURCE_PORT, "UPDATE public.%s SET title='pp' WHERE id=11" % TABLE), "返回 UPDATE 1", "UPDATE public.%s SET title='pp' WHERE id=11" % TABLE),
        sql("等待 node136 接收 node134 的 pp", "for i in $(seq 1 30); do v=$(%s -X -At -h 127.0.0.1 -p %s -U postgres -d postgres -c %r); test \"$v\" = pp && { echo \"$v\"; exit 0; }; sleep 1; done; exit 1" % (PSQL,OBSERVER_PORT,"SELECT title FROM public.%s WHERE id=11" % TABLE), "30 秒内输出 pp", "SELECT title FROM public.%s WHERE id=11" % TABLE,35),
        query("展示 node136 接收第一轮更新后的数据", OBSERVER_PORT, "SELECT id,title FROM public.%s WHERE id=11" % TABLE, "返回 id=11,title=pp", "11", "pp"),
        sql("按文档在 node135 更新同一行为 hh", psql(PEER_PORT, "UPDATE public.%s SET title='hh' WHERE id=11" % TABLE), "返回 UPDATE 1", "UPDATE public.%s SET title='hh' WHERE id=11" % TABLE),
        sql("等待 node136 记录 update_origin_change", "for i in $(seq 1 30); do v=$(%s -X -At -h 127.0.0.1 -p %s -U postgres -d postgres -c %r); test \"$v\" = 1 && { echo \"$v\"; exit 0; }; sleep 1; done; exit 1" % (PSQL,OBSERVER_PORT,"SELECT count(*) FROM fdd.mmr_conflict_history WHERE conflict_type='update_origin_change'"), "30 秒内存在一条 update_origin_change", "SELECT count(*) FROM fdd.mmr_conflict_history WHERE conflict_type='update_origin_change'",35),
        query("确认 node136 最终应用 node135 的 hh", OBSERVER_PORT, "SELECT id,title FROM public.%s WHERE id=11" % TABLE, "返回 id=11,title=hh", "11", "hh"),
        query("展示 update_origin_change 冲突记录", OBSERVER_PORT, "SELECT conflict_type,conflict_resolution,relname,apply_tuple FROM fdd.mmr_conflict_history WHERE conflict_type='update_origin_change' ORDER BY local_lsn DESC LIMIT 1", "返回 update_origin_change、update_if_newer、远端应用", "update_origin_change", "update_if_newer", TABLE, "remote"),
    ],
    "teardown": "以 immediate 停止临时 node134/node135/node136 PostgreSQL 实例；递归删除临时数据目录，连同冲突测试表、MMR 元数据、订阅、复制槽和本轮冲突记录一并删除。",
}
