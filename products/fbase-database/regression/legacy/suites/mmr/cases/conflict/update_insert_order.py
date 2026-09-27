from suites.mmr.cases.node_management.create_group import (PSQL, create_node,
                                                            init_instance, psql, shell)
from suites.mmr.cases.conflict.delete_missing import query, setup, sql
from suites.mmr.conflict_timing import parallel_transactions


ROOT = "/tmp/fbase_regress_mmr_conflict_update_insert_{run_id}"
A, B = ROOT + "/a", ROOT + "/b"
A_PORT, B_PORT = "15505", "15506"
DSN = "host=127.0.0.1 port=%s user=postgres dbname=postgres" % A_PORT
TABLE = "update_insert_{run_id}"


CASE = {
    "id": "mmr.conflict.update_insert_order", "name": "更新先提交、插入后提交的数据一致性",
    "document": "多活功能测试文档.md", "section": "4.3.1.1", "group": "conflict",
    "fixtures": ["cluster", {"type": "isolated_mmr_node_creation", "data_dir": ROOT}],
    "requirements": {"clusters": ["mmr"], "plugins": ["fdd_mmr"], "writable_node": True, "node": "mmr:mmr1"},
    "evidence_nodes": ["mmr:mmr1"],
    "prerequisites": ["初始 id=11 由 A/node134 写入并同步到 B/node135；两个事务均先完成 DML，A 的 COMMIT 先于 B。"],
    "steps": [
        setup(init_instance("初始化 A/node134", A, A_PORT)), setup(create_node(A_PORT, "node134")),
        setup(shell("创建测试表和初始 id=11 数据", psql(A_PORT, "CREATE TABLE public.%s(id int PRIMARY KEY,name varchar); INSERT INTO public.%s VALUES(11,'jone')" % (TABLE,TABLE)), "返回 CREATE TABLE 和 INSERT 0 1")),
        setup(shell("创建 g1 集群", psql(A_PORT, "SELECT fdd.create_group('g1')"), "返回 node group create successful")),
        setup(init_instance("初始化 B/node135", B, B_PORT)), setup(create_node(B_PORT, "node135")),
        setup(shell("以 all 模式将 B 加入 g1", psql(B_PORT, "SELECT fdd.join_group('g1','%s',true,'all','table_exist_error')" % DSN), "返回 node join to group complete finished", timeout=90)),
        query("展示 A 初始数据", A_PORT, "SELECT id,name FROM public.%s" % TABLE, "返回 id=11,jone", "11", "jone"),
        query("展示 B 初始数据", B_PORT, "SELECT id,name FROM public.%s" % TABLE, "返回 id=11,jone", "11", "jone"),
        query("确认触发前 A 没有旧 insert_exists 记录", A_PORT, "SELECT count(*) AS insert_exists_count FROM fdd.mmr_conflict_history WHERE conflict_type='insert_exists'", "返回 0", "0"),
        query("确认触发前 B 没有旧 update_pkey_exists 记录", B_PORT, "SELECT count(*) AS update_pkey_count FROM fdd.mmr_conflict_history WHERE conflict_type='update_pkey_exists'", "返回 0", "0"),
        sql("按文档让 A 更新先提交、B 插入后提交", parallel_transactions(PSQL, A_PORT, "UPDATE public.%s SET id=33 WHERE id=11" % TABLE, B_PORT, "INSERT INTO public.%s VALUES(33,'jone_new')" % TABLE, 1, 2), "A 返回 UPDATE 1 且先 COMMIT；B 返回 INSERT 0 1 且后 COMMIT", "-- A/node134\nBEGIN; UPDATE public.%s SET id=33 WHERE id=11; SELECT pg_sleep(1); COMMIT;\n-- B/node135\nBEGIN; INSERT INTO public.%s VALUES(33,'jone_new'); SELECT pg_sleep(2); COMMIT" % (TABLE,TABLE),35),
        sql("等待 A/B 均记录文档冲突", "for i in $(seq 1 30); do a=$(%s -X -At -h 127.0.0.1 -p %s -U postgres -d postgres -c %r); b=$(%s -X -At -h 127.0.0.1 -p %s -U postgres -d postgres -c %r); test \"$a|$b\" = '1|1' && { echo \"$a|$b\"; exit 0; }; sleep 1; done; exit 1" % (PSQL,A_PORT,"SELECT count(*) FROM fdd.mmr_conflict_history WHERE conflict_type='insert_exists'",PSQL,B_PORT,"SELECT count(*) FROM fdd.mmr_conflict_history WHERE conflict_type='update_pkey_exists'"), "30 秒内 A=1、B=1", "SELECT count(*) FROM fdd.mmr_conflict_history WHERE conflict_type IN ('insert_exists','update_pkey_exists')",35),
        query("展示 A 的 insert_exists 冲突记录", A_PORT, "SELECT conflict_type,conflict_resolution,relname,apply_tuple FROM fdd.mmr_conflict_history WHERE conflict_type='insert_exists' ORDER BY local_lsn DESC LIMIT 1", "返回 insert_exists、update_if_newer 和 remote", "insert_exists", "update_if_newer", "remote"),
        query("展示 B 的 update_pkey_exists 冲突记录", B_PORT, "SELECT conflict_type,conflict_resolution,relname,apply_tuple FROM fdd.mmr_conflict_history WHERE conflict_type='update_pkey_exists' ORDER BY local_lsn DESC LIMIT 1", "返回 update_pkey_exists、update_if_newer", "update_pkey_exists", "update_if_newer"),
        query("展示 A 最终数据", A_PORT, "SELECT id,name FROM public.%s ORDER BY id" % TABLE, "返回 id=33,jone_new", "33", "jone_new"),
        query("展示 B 最终数据", B_PORT, "SELECT id,name FROM public.%s ORDER BY id" % TABLE, "展示文档规定的不一致结果", "11", "33", "jone_new"),
    ],
    "teardown": "以 immediate 停止临时 A/node134 和 B/node135 PostgreSQL 实例；递归删除临时数据目录，连同测试表、MMR 元数据、订阅、复制槽和本轮冲突记录一并删除。",
}
