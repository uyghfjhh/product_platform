from suites.mmr.cases.conflict.delete_missing import query, setup, sql
from suites.mmr.cases.node_management.create_group import (PSQL, create_node,
                                                            init_instance, psql, shell)
from suites.mmr.conflict_timing import parallel_transactions


ROOT = "/tmp/fbase_regress_mmr_conflict_update_delete_{run_id}"
A, B = ROOT + "/a", ROOT + "/b"
A_PORT, B_PORT = "15517", "15518"
DSN = "host=127.0.0.1 port=%s user=postgres dbname=postgres" % A_PORT
DELETE_UPDATE, UPDATE_DELETE, DELETE_PKEY, PKEY_DELETE = (
    "update_delete_%s_{run_id}" % name for name in ("delete_update", "update_delete", "delete_pkey", "pkey_delete"))


def wait_for(title, port, statement, expected, timeout=35):
    return sql(title, "for i in $(seq 1 30); do v=$(%s -X -At -h 127.0.0.1 -p %s -U postgres -d postgres -c %r); test \"$v\" = %r && { echo \"$v\"; exit 0; }; sleep 1; done; exit 1" % (PSQL, port, statement, expected), "30 秒内输出 %s" % expected, statement, timeout)


def rows(title, port, table, expected, *markers):
    return query(title, port, "SELECT id,name,age FROM public.%s ORDER BY id" % table, expected, *markers)


def hist(title, port, table, expected, *markers):
    return query(title, port, "SELECT conflict_type,conflict_resolution,relname,apply_tuple FROM fdd.mmr_conflict_history WHERE relname='%s' ORDER BY local_lsn" % table, expected, *markers)


def document_evidence(step):
    return step


def before(label, table, *markers):
    return [rows("展示%s触发前 A 初始数据" % label, A_PORT, table, "展示文档规定初始数据", *markers), rows("展示%s触发前 B 初始数据" % label, B_PORT, table, "展示文档规定初始数据", *markers), query("确认%s触发前 A 没有旧冲突记录" % label, A_PORT, "SELECT count(*) AS conflict_count FROM fdd.mmr_conflict_history WHERE relname='%s'" % table, "返回 0", "0"), query("确认%s触发前 B 没有旧冲突记录" % label, B_PORT, "SELECT count(*) AS conflict_count FROM fdd.mmr_conflict_history WHERE relname='%s'" % table, "返回 0", "0")]


def race(title, a_sql, b_sql, expected, display):
    return sql(title, parallel_transactions(PSQL, A_PORT, a_sql, B_PORT, b_sql, 1, 2), expected, display, 35)


DDL = "; ".join(["CREATE TABLE public.%s(id int PRIMARY KEY,name text,age text)" % t for t in (DELETE_UPDATE,UPDATE_DELETE,DELETE_PKEY,PKEY_DELETE)] + ["INSERT INTO public.%s VALUES%s" % item for item in [(DELETE_UPDATE,"(11,'AAA','22')"),(UPDATE_DELETE,"(11,'AAA','22')"),(DELETE_PKEY,"(11,'AAA','22')"),(PKEY_DELETE,"(22,'AA','22')")]])


CASE = {
    "id": "mmr.conflict.update_delete", "name": "UPDATE-DELETE 冲突及死行搜索时间", "document": "多活功能测试文档.md", "section": "4.3.4.1,4.3.4.2", "group": "conflict",
    "fixtures": ["cluster", {"type": "isolated_mmr_node_creation", "data_dir": ROOT}],
    "requirements": {"clusters": ["mmr"], "plugins": ["fdd_mmr"], "writable_node": True, "node": "mmr:mmr1"}, "evidence_nodes": ["mmr:mmr1"],
    "prerequisites": ["隔离 A/node134、B/node135 采用 all 模式组成两节点组；四张表分别隔离不改主键的两个方向、改主键默认搜索时间和 20ms 搜索时间。", "每个场景 A 先、B 后提交，采用独立 psql 事务精确控制顺序。"],
    "steps": [
        setup(init_instance("初始化 A/node134", A, A_PORT)), setup(create_node(A_PORT, "node134")), setup(shell("创建四张 UPDATE-DELETE 测试表及等价初始数据", psql(A_PORT, DDL), "返回四次 CREATE TABLE 和四次 INSERT 0 1")), setup(shell("创建 g1 集群", psql(A_PORT, "SELECT fdd.create_group('g1')"), "返回 node group create successful")), setup(init_instance("初始化 B/node135", B, B_PORT)), setup(create_node(B_PORT, "node135")), setup(shell("以 all 模式将 B 加入 g1", psql(B_PORT, "SELECT fdd.join_group('g1','%s',true,'all','table_exist_error')" % DSN), "返回 node join to group complete finished", timeout=90)),
        query("读取 A/node134 实际 debug_logical_replication_streaming", A_PORT, "SHOW debug_logical_replication_streaming", "返回 buffered", "buffered"), query("读取 B/node135 实际 debug_logical_replication_streaming", B_PORT, "SHOW debug_logical_replication_streaming", "返回 buffered", "buffered"), query("读取 A/node134 实际 logical_decoding_work_mem", A_PORT, "SHOW logical_decoding_work_mem", "返回 64MB", "64MB"), query("读取 B/node135 实际 logical_decoding_work_mem", B_PORT, "SHOW logical_decoding_work_mem", "返回 64MB", "64MB"), query("读取 A/node134 的多活 streaming 模式", A_PORT, "SELECT CASE streaming WHEN 'f' THEN 'off' WHEN 't' THEN 'on' WHEN 'p' THEN 'parallel' END FROM fdd.mmr_node WHERE node_name='node134'", "返回 off", "off"), query("读取 B/node135 的多活 streaming 模式", B_PORT, "SELECT CASE streaming WHEN 'f' THEN 'off' WHEN 't' THEN 'on' WHEN 'p' THEN 'parallel' END FROM fdd.mmr_node WHERE node_name='node135'", "返回 off", "off"),
    ] + before("4.3.4.1 场景一：A DELETE、B UPDATE", DELETE_UPDATE, "11", "AAA", "22", "(1 row)") + [
        race("不改主键场景一：A 删除、B 更新，A 先提交", "DELETE FROM public.%s WHERE id=11" % DELETE_UPDATE, "UPDATE public.%s SET name='AAA_new' WHERE id=11" % DELETE_UPDATE, "A DELETE 1 先 COMMIT；B UPDATE 1 后 COMMIT", "-- A/node134\nBEGIN; DELETE FROM public.%s WHERE id=11; SELECT pg_sleep(1); COMMIT;\n-- B/node135\nBEGIN; UPDATE public.%s SET name='AAA_new' WHERE id=11; SELECT pg_sleep(2); COMMIT" % (DELETE_UPDATE,DELETE_UPDATE)),
        wait_for("等待场景一 A 记录 update_missing", A_PORT, "SELECT count(*) FROM fdd.mmr_conflict_history WHERE relname='%s' AND conflict_type='update_missing'" % DELETE_UPDATE, "1"), wait_for("等待场景一 B 记录 delete_recently_updated", B_PORT, "SELECT count(*) FROM fdd.mmr_conflict_history WHERE relname='%s' AND conflict_type='delete_recently_updated'" % DELETE_UPDATE, "1"), hist("展示场景一 A 完整冲突记录", A_PORT, DELETE_UPDATE, "返回 update_missing、insert_or_skip、remote", "update_missing", "insert_or_skip", "remote"), hist("展示场景一 B 完整冲突记录", B_PORT, DELETE_UPDATE, "返回 delete_recently_updated、skip、none", "delete_recently_updated", "skip", "none"), rows("展示场景一 A 最终数据", A_PORT, DELETE_UPDATE, "返回 id=11,AAA_new,22", "11", "AAA_new", "22", "(1 row)"), rows("展示场景一 B 最终数据", B_PORT, DELETE_UPDATE, "返回 id=11,AAA_new,22", "11", "AAA_new", "22", "(1 row)"),
    ] + before("4.3.4.1 场景二：A UPDATE、B DELETE", UPDATE_DELETE, "11", "AAA", "22", "(1 row)") + [
        race("不改主键场景二：A 更新、B 删除，A 先提交", "UPDATE public.%s SET name='AAA_new' WHERE id=11" % UPDATE_DELETE, "DELETE FROM public.%s WHERE id=11" % UPDATE_DELETE, "A UPDATE 1 先 COMMIT；B DELETE 1 后 COMMIT", "-- A/node134\nBEGIN; UPDATE public.%s SET name='AAA_new' WHERE id=11; SELECT pg_sleep(1); COMMIT;\n-- B/node135\nBEGIN; DELETE FROM public.%s WHERE id=11; SELECT pg_sleep(2); COMMIT" % (UPDATE_DELETE,UPDATE_DELETE)),
        wait_for("等待场景二 B 记录 update_recently_deleted", B_PORT, "SELECT count(*) FROM fdd.mmr_conflict_history WHERE relname='%s' AND conflict_type='update_recently_deleted'" % UPDATE_DELETE, "1"), query("确认场景二 A 没有冲突记录", A_PORT, "SELECT count(*) AS conflict_count FROM fdd.mmr_conflict_history WHERE relname='%s'" % UPDATE_DELETE, "返回 0", "0"), hist("展示场景二 B 完整冲突记录", B_PORT, UPDATE_DELETE, "返回 update_recently_deleted、skip、none", "update_recently_deleted", "skip", "none"), rows("展示场景二 A 最终数据", A_PORT, UPDATE_DELETE, "返回 0 行", "(0 rows)"), rows("展示场景二 B 最终数据", B_PORT, UPDATE_DELETE, "返回 0 行", "(0 rows)"),
    ] + before("4.3.4.2 场景一：默认 30s 搜索时间", DELETE_PKEY, "11", "AAA", "22", "(1 row)") + [
        query("读取默认 fdd.search_dead_tup_time_interval", A_PORT, "SHOW fdd.search_dead_tup_time_interval", "返回 30s", "30s"),
        race("改主键场景一：A 删除、B 更新为 id=22，A 先提交", "DELETE FROM public.%s WHERE id=11" % DELETE_PKEY, "UPDATE public.%s SET id=22 WHERE id=11" % DELETE_PKEY, "A DELETE 1 先 COMMIT；B UPDATE 1 后 COMMIT", "-- A/node134\nBEGIN; DELETE FROM public.%s WHERE id=11; SELECT pg_sleep(1); COMMIT;\n-- B/node135\nBEGIN; UPDATE public.%s SET id=22 WHERE id=11; SELECT pg_sleep(2); COMMIT" % (DELETE_PKEY,DELETE_PKEY)),
        wait_for("等待默认搜索时间场景 A 记录 update_missing", A_PORT, "SELECT count(*) FROM fdd.mmr_conflict_history WHERE relname='%s' AND conflict_type='update_missing'" % DELETE_PKEY, "1"), wait_for("等待默认搜索时间场景 B 记录 delete_missing", B_PORT, "SELECT count(*) FROM fdd.mmr_conflict_history WHERE relname='%s' AND conflict_type='delete_missing'" % DELETE_PKEY, "1"), hist("展示默认搜索时间场景 A 完整冲突记录", A_PORT, DELETE_PKEY, "返回 update_missing、insert_or_skip、remote", "update_missing", "insert_or_skip", "remote"), hist("展示默认搜索时间场景 B 完整冲突记录", B_PORT, DELETE_PKEY, "返回 delete_missing、skip、none", "delete_missing", "skip", "none"), rows("展示默认搜索时间场景 A 最终数据", A_PORT, DELETE_PKEY, "返回 id=22,AAA,22", "22", "AAA", "22", "(1 row)"), rows("展示默认搜索时间场景 B 最终数据", B_PORT, DELETE_PKEY, "返回 id=22,AAA,22", "22", "AAA", "22", "(1 row)"),
    ] + before("4.3.4.2 场景二：20ms 搜索时间", PKEY_DELETE, "22", "AA", "22", "(1 row)") + [
        sql("按文档在 node134 设置死行搜索时间为 20ms", psql(A_PORT, "ALTER SYSTEM SET fdd.search_dead_tup_time_interval='20ms'"), "返回 ALTER SYSTEM", "ALTER SYSTEM SET fdd.search_dead_tup_time_interval='20ms';", report_node="node134"),
        sql("按文档 reload node134 的死行搜索时间配置", psql(A_PORT, "SELECT pg_reload_conf()"), "返回 true", "SELECT pg_reload_conf();", report_node="node134"),
        sql("按文档在 node135 设置死行搜索时间为 20ms", psql(B_PORT, "ALTER SYSTEM SET fdd.search_dead_tup_time_interval='20ms'"), "返回 ALTER SYSTEM", "ALTER SYSTEM SET fdd.search_dead_tup_time_interval='20ms';", report_node="node135"),
        sql("按文档 reload node135 的死行搜索时间配置", psql(B_PORT, "SELECT pg_reload_conf()"), "返回 true", "SELECT pg_reload_conf();", report_node="node135"),
        query("展示 A 实际 fdd.search_dead_tup_time_interval", A_PORT, "SHOW fdd.search_dead_tup_time_interval", "返回 20ms", "20ms"), query("展示 B 实际 fdd.search_dead_tup_time_interval", B_PORT, "SHOW fdd.search_dead_tup_time_interval", "返回 20ms", "20ms"),
        race("20ms 场景：A 主键更新为 33、B 删除，A 先提交", "UPDATE public.%s SET id=33 WHERE id=22" % PKEY_DELETE, "DELETE FROM public.%s WHERE id=22" % PKEY_DELETE, "A UPDATE 1 先 COMMIT；B DELETE 1 后 COMMIT", "-- A/node134\nBEGIN; UPDATE public.%s SET id=33 WHERE id=22; SELECT pg_sleep(1); COMMIT;\n-- B/node135\nBEGIN; DELETE FROM public.%s WHERE id=22; SELECT pg_sleep(2); COMMIT" % (PKEY_DELETE,PKEY_DELETE)),
        wait_for("等待 20ms 场景 A 记录 delete_missing", A_PORT, "SELECT count(*) FROM fdd.mmr_conflict_history WHERE relname='%s' AND conflict_type='delete_missing'" % PKEY_DELETE, "1"), wait_for("等待 20ms 场景 B 记录 update_recently_deleted", B_PORT, "SELECT count(*) FROM fdd.mmr_conflict_history WHERE relname='%s' AND conflict_type='update_recently_deleted'" % PKEY_DELETE, "1"), hist("展示 20ms 场景 A 完整冲突记录", A_PORT, PKEY_DELETE, "返回 delete_missing、skip、none", "delete_missing", "skip", "none"), hist("展示 20ms 场景 B 完整冲突记录", B_PORT, PKEY_DELETE, "返回 update_recently_deleted、skip、none", "update_recently_deleted", "skip", "none"), document_evidence(rows("展示 20ms 场景 A 最终数据", A_PORT, PKEY_DELETE, "返回 id=33,AA,22", "33", "AA", "22", "(1 row)")), document_evidence(rows("展示 20ms 场景 B 最终数据", B_PORT, PKEY_DELETE, "返回 0 行", "(0 rows)")),
    ],
    "teardown": "以 immediate 停止临时 A/node134 和 B/node135 PostgreSQL 实例；递归删除临时数据目录，连同四张 UPDATE-DELETE 测试表、临时 ALTER SYSTEM 配置、MMR 元数据、订阅、复制槽和本轮冲突记录一并删除。",
}
