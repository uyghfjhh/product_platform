from framework.assertions import output_contains_text
from suites.mmr.cases.node_management.create_group import (PSQL, create_node,
                                                            init_instance, psql, shell)
from suites.mmr.cases.conflict.delete_missing import query, setup, sql


ROOT = "/tmp/fbase_regress_mmr_conflict_recent_delete_{run_id}"
SOURCE, TARGET = ROOT + "/source", ROOT + "/target"
SOURCE_PORT, TARGET_PORT = "15496", "15497"
DSN = "host=127.0.0.1 port=%s user=postgres dbname=postgres" % SOURCE_PORT
TABLE, SET = "recent_delete_{run_id}", "set_recent_{run_id}"


def controlled_recent_delete():
    """Match mmr-autotest's confirmed DELETE -> UPDATE -> COMMIT sequence."""
    lock_key = 2026072001
    delete_sql = "DELETE FROM public.%s WHERE id=1" % TABLE
    update_sql = "UPDATE public.%s SET id=3 WHERE id=1" % TABLE
    command = (
        "delete_log=%r; rm -f \"$delete_log\"; "
        "%s -X -v ON_ERROR_STOP=1 -P pager=off -h 127.0.0.1 -p %s -U postgres -d postgres -c %r >\"$delete_log\" 2>&1 & delete_pid=$!; "
        "ready=; for attempt in $(seq 1 100); do "
        "if ! kill -0 \"$delete_pid\" 2>/dev/null; then cat \"$delete_log\"; wait \"$delete_pid\"; exit 1; fi; "
        "ready=$(%s -X -At -h 127.0.0.1 -p %s -U postgres -d postgres -c %r 2>/dev/null || true); "
        "test \"$ready\" = f && break; sleep 0.1; done; "
        "test \"$ready\" = f || { echo 'node135 DELETE transaction was not ready'; kill \"$delete_pid\"; wait \"$delete_pid\" || true; exit 1; }; "
        "%s -X -v ON_ERROR_STOP=1 -P pager=off -h 127.0.0.1 -p %s -U postgres -d postgres -c %r; "
        "sleep 1; wait \"$delete_pid\"; cat \"$delete_log\"; rm -f \"$delete_log\"" %
        (ROOT + "/delete_transaction.log", PSQL, TARGET_PORT,
         "BEGIN; %s; SELECT pg_advisory_xact_lock(%s); SELECT pg_sleep(4); COMMIT" %
         (delete_sql, lock_key), PSQL, TARGET_PORT,
         "SELECT pg_try_advisory_xact_lock(%s)" % lock_key,
         PSQL, SOURCE_PORT, update_sql))
    step = shell(
        "按 mmr-autotest 时序触发 update_recently_deleted", command,
        "确认 node135 DELETE 1 未提交后，node134 UPDATE 1 完成；等待 1 秒后 node135 COMMIT",
        output_contains_text("DELETE 1", "UPDATE 1", "COMMIT"), timeout=40)
    step["display_sql"] = (
        "-- node135，同一会话执行并保持事务；事务锁确认 DELETE 已完成\n"
        "BEGIN;\n%s;\nSELECT pg_advisory_xact_lock(%s);\n"
        "-- node134，仅在确认 node135 DELETE 1 后执行\n%s;\n"
        "-- 等待 1 秒后 node135\nCOMMIT;" % (delete_sql, lock_key, update_sql))
    step["report_node"] = "node134/node135"
    return step


CASE = {
    "id": "mmr.conflict.update_recently_deleted", "name": "update_recently_deleted 默认 skip 冲突处理",
    "document": "多活功能测试文档.md", "section": "4.1.9", "group": "conflict",
    "fixtures": ["cluster", {"type": "isolated_mmr_node_creation", "data_dir": ROOT}],
    "requirements": {"clusters": ["mmr"], "plugins": ["fdd_mmr"], "writable_node": True, "node": "mmr:mmr1"},
    "evidence_nodes": ["mmr:mmr1"],
    "prerequisites": ["私有 set_recent 复制 INSERT/UPDATE 但不复制 DELETE，使 node135 的本地删除不回传 node134。", "按 mmr-autotest 保证 node135 DELETE 的提交时间晚于 node134 UPDATE，满足 update_recently_deleted 的源码判定条件。"],
    "steps": [
        setup(init_instance("初始化 node134", SOURCE, SOURCE_PORT)), setup(create_node(SOURCE_PORT, "node134")),
        setup(shell("创建建组探针表", psql(SOURCE_PORT, "CREATE TABLE join_probe(id int PRIMARY KEY)"), "返回 CREATE TABLE")),
        setup(shell("创建 g1 集群", psql(SOURCE_PORT, "SELECT fdd.create_group('g1')"), "返回 node group create successful")),
        setup(init_instance("初始化 node135", TARGET, TARGET_PORT)), setup(create_node(TARGET_PORT, "node135")),
        setup(shell("以 all 模式加入 g1", psql(TARGET_PORT, "SELECT fdd.join_group('g1','%s',true,'all','table_exist_error')" % DSN), "返回 node join to group complete finished", timeout=90)),
        setup(shell("两端创建同名 test2 表", psql(SOURCE_PORT, "CREATE TABLE public.%s(id int PRIMARY KEY,name name)" % TABLE) + "; " + psql(TARGET_PORT, "CREATE TABLE public.%s(id int PRIMARY KEY,name name)" % TABLE), "两个成员返回 CREATE TABLE")),
        setup(shell("创建不复制 DELETE 的私有复制集", psql(SOURCE_PORT, "SELECT fdd.create_replication_set('%s',true,true,false,true,false,false,false,false,true)" % SET) + "; " + psql(SOURCE_PORT, "SELECT fdd.replication_set_add_table('public.%s'::regclass,'%s',false,false)" % (TABLE,SET)) + "; " + psql(SOURCE_PORT, "SELECT fdd.run_on_all_nodes('SELECT fdd.alter_node_replication_sets(''{%s}'')')" % SET) + "; " + psql(SOURCE_PORT, "SELECT fdd.replication_set_async_execute(true)"), "私有复制集绑定成功", timeout=90)),
        setup(shell("源端插入两行并等待目标端同步", psql(SOURCE_PORT, "INSERT INTO public.%s VALUES(1,'aa'),(2,'aa')" % TABLE) + "; for i in $(seq 1 30); do test \"$(%s -X -At -h 127.0.0.1 -p %s -U postgres -d postgres -c %r)\" = 2 && exit 0; sleep 1; done; exit 1" % (PSQL,TARGET_PORT,"SELECT count(*) FROM public.%s" % TABLE), "两端均有两行初始数据", timeout=35)),
        query("读取发布端实际 debug_logical_replication_streaming", SOURCE_PORT, "SHOW debug_logical_replication_streaming", "返回 buffered", "buffered"),
        query("读取订阅端实际 debug_logical_replication_streaming", TARGET_PORT, "SHOW debug_logical_replication_streaming", "返回 buffered", "buffered"),
        query("展示 node134 触发前 test2 数据", SOURCE_PORT, "SELECT id,name FROM public.%s ORDER BY id" % TABLE, "返回 id=1、2", "1", "2"),
        query("展示 node135 触发前 test2 数据", TARGET_PORT, "SELECT id,name FROM public.%s ORDER BY id" % TABLE, "返回 id=1、2", "1", "2"),
        query("确认触发前没有旧的 update_recently_deleted 记录", TARGET_PORT, "SELECT count(*) AS recent_delete_count FROM fdd.mmr_conflict_history WHERE conflict_type='update_recently_deleted'", "返回 0", "0"),
        controlled_recent_delete(),
        sql("等待 node135 记录 update_recently_deleted", "for i in $(seq 1 30); do v=$(%s -X -At -h 127.0.0.1 -p %s -U postgres -d postgres -c %r); test \"$v\" = 1 && { echo \"$v\"; exit 0; }; sleep 1; done; exit 1" % (PSQL,TARGET_PORT,"SELECT count(*) FROM fdd.mmr_conflict_history WHERE conflict_type='update_recently_deleted'"), "30 秒内存在一条 update_recently_deleted", "SELECT count(*) FROM fdd.mmr_conflict_history WHERE conflict_type='update_recently_deleted'", 35),
        query("展示 update_recently_deleted 冲突记录", TARGET_PORT, "SELECT conflict_type,conflict_resolution,relname,apply_tuple FROM fdd.mmr_conflict_history WHERE conflict_type='update_recently_deleted' ORDER BY local_lsn DESC LIMIT 1", "返回 update_recently_deleted、skip 和 none", "update_recently_deleted", "skip", TABLE, "none"),
        query("展示 node135 最终数据", TARGET_PORT, "SELECT id,name FROM public.%s ORDER BY id" % TABLE, "仅返回 id=2", "2", "aa", "(1 row)"),
        query("按文档断言 node135 仅保留 id=2", TARGET_PORT, "SELECT (count(*)=1 AND min(id)=2 AND max(id)=2)::text AS only_id_2 FROM public.%s" % TABLE, "返回 true", "true"),
    ],
    "teardown": "以 immediate 停止临时 node134/node135 PostgreSQL 实例；递归删除临时数据目录，连同 join_probe、test2 表、私有复制集、MMR 元数据、订阅、复制槽和本轮冲突记录一并删除。",
}
