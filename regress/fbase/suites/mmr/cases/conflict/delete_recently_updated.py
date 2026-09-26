from suites.mmr.cases.node_management.create_group import (PSQL, create_node,
                                                            init_instance, psql, shell)
from suites.mmr.cases.conflict.delete_missing import query, setup, sql


ROOT = "/tmp/fbase_regress_mmr_conflict_recent_update_{run_id}"
SOURCE, TARGET = ROOT + "/source", ROOT + "/target"
SOURCE_PORT, TARGET_PORT = "15501", "15502"
DSN = "host=127.0.0.1 port=%s user=postgres dbname=postgres" % SOURCE_PORT
TABLE = "recent_update_{run_id}"


CASE = {
    "id": "mmr.conflict.delete_recently_updated", "name": "delete_recently_updated 默认 skip 冲突处理",
    "document": "多活功能测试文档.md", "section": "4.1.11", "group": "conflict",
    "fixtures": ["cluster", {"type": "isolated_mmr_node_creation", "data_dir": ROOT}],
    "requirements": {"clusters": ["mmr"], "plugins": ["fdd_mmr"], "writable_node": True, "node": "mmr:mmr1"},
    "evidence_nodes": ["mmr:mmr1"],
    "prerequisites": ["两节点已同步 id=26,name=1_b；随后两个独立 psql 进程并发执行源端 UPDATE 和目标端 DELETE。"],
    "steps": [
        setup(init_instance("初始化 node134", SOURCE, SOURCE_PORT)), setup(create_node(SOURCE_PORT, "node134")),
        setup(shell("创建冲突测试表", psql(SOURCE_PORT, "CREATE TABLE public.%s(id int PRIMARY KEY,name text)" % TABLE), "返回 CREATE TABLE")),
        setup(shell("创建 g1 集群", psql(SOURCE_PORT, "SELECT fdd.create_group('g1')"), "返回 node group create successful")),
        setup(init_instance("初始化 node135", TARGET, TARGET_PORT)), setup(create_node(TARGET_PORT, "node135")),
        setup(shell("以 all 模式将 node135 加入 g1", psql(TARGET_PORT, "SELECT fdd.join_group('g1','%s',true,'all','table_exist_error')" % DSN), "返回 node join to group complete finished", timeout=90)),
        setup(shell("插入 id=26 并等待同步", psql(SOURCE_PORT, "INSERT INTO public.%s VALUES(26,'1_b')" % TABLE) + "; for i in $(seq 1 30); do test \"$(%s -X -At -h 127.0.0.1 -p %s -U postgres -d postgres -c %r)\" = 1_b && exit 0; sleep 1; done; exit 1" % (PSQL,TARGET_PORT,"SELECT name FROM public.%s WHERE id=26" % TABLE), "两端均有 id=26", timeout=35)),
        query("读取 node134 实际 debug_logical_replication_streaming", SOURCE_PORT, "SHOW debug_logical_replication_streaming", "返回 buffered", "buffered"),
        query("读取 node135 实际 debug_logical_replication_streaming", TARGET_PORT, "SHOW debug_logical_replication_streaming", "返回 buffered", "buffered"),
        query("展示 node134 并发前数据", SOURCE_PORT, "SELECT id,name FROM public.%s" % TABLE, "返回 id=26,name=1_b", "26", "1_b"),
        query("展示 node135 并发前数据", TARGET_PORT, "SELECT id,name FROM public.%s" % TABLE, "返回 id=26,name=1_b", "26", "1_b"),
        query("确认触发前没有旧的 delete_recently_updated 记录", SOURCE_PORT, "SELECT count(*) AS recent_update_count FROM fdd.mmr_conflict_history WHERE conflict_type='delete_recently_updated'", "返回 0", "0"),
        sql("按 mmr-autotest 时序执行 node134 UPDATE 与 node135 DELETE", "update_log=/tmp/fbase_regress_delete_recently_updated.log; rm -f \"$update_log\"; " + psql(SOURCE_PORT, "BEGIN; UPDATE public.%s SET name='bnbb' WHERE id=26; SELECT pg_advisory_xact_lock(380000026); SELECT pg_sleep(3); COMMIT" % TABLE) + " >\"$update_log\" 2>&1 & update_pid=$!; ready=; for attempt in $(seq 1 100); do if ! kill -0 \"$update_pid\" 2>/dev/null; then cat \"$update_log\"; wait \"$update_pid\"; exit 1; fi; ready=$(" + PSQL + " -X -At -h 127.0.0.1 -p " + SOURCE_PORT + " -U postgres -d postgres -c 'SELECT pg_try_advisory_xact_lock(380000026)' 2>/dev/null || true); test \"$ready\" = f && break; sleep 0.1; done; test \"$ready\" = f || { echo 'node134 UPDATE transaction was not ready'; kill \"$update_pid\"; wait \"$update_pid\" || true; exit 1; }; " + psql(TARGET_PORT, "DELETE FROM public.%s WHERE id=26" % TABLE) + "; sleep 1; update_status=0; wait \"$update_pid\" || update_status=$?; cat \"$update_log\"; rm -f \"$update_log\"; exit \"$update_status\"", "node134 UPDATE 已执行未提交后，node135 DELETE 成功；等待 1 秒后 node134 COMMIT", "-- node134：先更新并保持事务\nBEGIN; UPDATE public.%s SET name='bnbb' WHERE id=26;\n-- node135：node134 事务保持期间删除\nDELETE FROM public.%s WHERE id=26;\n-- 等待 1 秒后 node134\nCOMMIT;" % (TABLE,TABLE), 35),
        sql("等待 node134 记录 delete_recently_updated", "for i in $(seq 1 30); do v=$(%s -X -At -h 127.0.0.1 -p %s -U postgres -d postgres -c %r); test \"$v\" = 1 && { echo \"$v\"; exit 0; }; sleep 1; done; exit 1" % (PSQL,SOURCE_PORT,"SELECT count(*) FROM fdd.mmr_conflict_history WHERE conflict_type='delete_recently_updated'"), "30 秒内存在一条 delete_recently_updated", "SELECT count(*) FROM fdd.mmr_conflict_history WHERE conflict_type='delete_recently_updated'",35),
        query("展示 delete_recently_updated 冲突记录", SOURCE_PORT, "SELECT conflict_type,conflict_resolution,relname,apply_tuple FROM fdd.mmr_conflict_history WHERE conflict_type='delete_recently_updated' ORDER BY local_lsn DESC LIMIT 1", "返回 delete_recently_updated、skip 和 none", "delete_recently_updated", "skip", TABLE, "none"),
    ],
    "teardown": "以 immediate 停止临时 node134/node135 PostgreSQL 实例；递归删除临时数据目录，连同冲突测试表、MMR 元数据、订阅、复制槽和本轮冲突记录一并删除。",
}
