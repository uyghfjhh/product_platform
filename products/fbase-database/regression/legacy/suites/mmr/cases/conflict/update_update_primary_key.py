from suites.mmr.cases.conflict.delete_missing import query, setup, sql
from suites.mmr.cases.node_management.create_group import (PSQL, create_node,
                                                            init_instance, psql, shell)
from suites.mmr.conflict_timing import parallel_transactions


ROOT = "/tmp/fbase_regress_mmr_conflict_update_update_pk_{run_id}"
A, B = ROOT + "/a", ROOT + "/b"
A_PORT, B_PORT = "15509", "15510"
DSN = "host=127.0.0.1 port=%s user=postgres dbname=postgres" % A_PORT
SAME = "update_update_same_{run_id}"
DIFF = "update_update_diff_{run_id}"
VALUE = "update_update_value_{run_id}"


def wait_for(title, port, statement, expected, timeout=35):
    return sql(title,
               "for i in $(seq 1 30); do v=$(%s -X -At -h 127.0.0.1 -p %s -U postgres -d postgres -c %r); test \"$v\" = %r && { echo \"$v\"; exit 0; }; sleep 1; done; exit 1" %
               (PSQL, port, statement, expected), "30 秒内输出 %s" % expected, statement, timeout)


def history(title, port, table, expected, *markers):
    return query(title, port, "SELECT conflict_type,conflict_resolution,relname,apply_tuple FROM fdd.mmr_conflict_history WHERE relname='%s' ORDER BY local_lsn" % table, expected, *markers)


def data(title, port, table, expected, *markers):
    return query(title, port, "SELECT id,name FROM public.%s ORDER BY id" % table, expected, *markers)


CASE = {
    "id": "mmr.conflict.update_update_primary_key",
    "name": "主键复制标识的 UPDATE-UPDATE 冲突",
    "document": "多活功能测试文档.md", "section": "4.3.2.1", "group": "conflict",
    "fixtures": ["cluster", {"type": "isolated_mmr_node_creation", "data_dir": ROOT}],
    "requirements": {"clusters": ["mmr"], "plugins": ["fdd_mmr"], "writable_node": True, "node": "mmr:mmr1"},
    "evidence_nodes": ["mmr:mmr1"],
    "prerequisites": ["隔离 A/node134、B/node135 采用 all 模式组成两节点组；三张表隔离同主键、不同主键及非主键更新场景。", "两个节点使用相同系统时区；所有并发事务以 pg_sleep 固定文档列出的提交先后。"],
    "steps": [
        setup(init_instance("初始化 A/node134", A, A_PORT)), setup(create_node(A_PORT, "node134")),
        setup(shell("创建三个 UPDATE-UPDATE 冲突测试表", psql(A_PORT, "CREATE TABLE public.%s(id int PRIMARY KEY,name text); CREATE TABLE public.%s(id int PRIMARY KEY,name text); CREATE TABLE public.%s(id int PRIMARY KEY,name text); INSERT INTO public.%s VALUES(12,'1_b'); INSERT INTO public.%s VALUES(11,'B'); INSERT INTO public.%s VALUES(2,'bnbb')" % (SAME,DIFF,VALUE,SAME,DIFF,VALUE)), "返回 CREATE TABLE 和三次 INSERT 0 1")),
        setup(shell("创建 g1 集群", psql(A_PORT, "SELECT fdd.create_group('g1')"), "返回 node group create successful")),
        setup(init_instance("初始化 B/node135", B, B_PORT)), setup(create_node(B_PORT, "node135")),
        setup(shell("以 all 模式将 B 加入 g1", psql(B_PORT, "SELECT fdd.join_group('g1','%s',true,'all','table_exist_error')" % DSN), "返回 node join to group complete finished", timeout=90)),
        query("读取 A/node134 实际 debug_logical_replication_streaming", A_PORT, "SHOW debug_logical_replication_streaming", "返回 buffered", "buffered"),
        query("读取 B/node135 实际 debug_logical_replication_streaming", B_PORT, "SHOW debug_logical_replication_streaming", "返回 buffered", "buffered"),
        query("读取 A/node134 实际 logical_decoding_work_mem", A_PORT, "SHOW logical_decoding_work_mem", "返回 64MB", "64MB"),
        query("读取 B/node135 实际 logical_decoding_work_mem", B_PORT, "SHOW logical_decoding_work_mem", "返回 64MB", "64MB"),
        query("读取 A/node134 的多活 streaming 模式", A_PORT, "SELECT CASE streaming WHEN 'f' THEN 'off' WHEN 't' THEN 'on' WHEN 'p' THEN 'parallel' END FROM fdd.mmr_node WHERE node_name='node134'", "返回 off", "off"),
        query("读取 B/node135 的多活 streaming 模式", B_PORT, "SELECT CASE streaming WHEN 'f' THEN 'off' WHEN 't' THEN 'on' WHEN 'p' THEN 'parallel' END FROM fdd.mmr_node WHERE node_name='node135'", "返回 off", "off"),
        data("展示场景一触发前 A 初始数据", A_PORT, SAME, "返回 id=12,name=1_b", "12", "1_b"),
        data("展示场景一触发前 B 初始数据", B_PORT, SAME, "返回 id=12,name=1_b", "12", "1_b"),
        query("确认场景一触发前没有旧冲突记录", A_PORT, "SELECT count(*) AS conflict_count FROM fdd.mmr_conflict_history WHERE relname='%s'" % SAME, "返回 0", "0"),
        sql("场景一并发更新为相同主键 11，B 后提交", parallel_transactions(PSQL, A_PORT, "UPDATE public.%s SET id=11,name='A' WHERE id=12" % SAME, B_PORT, "UPDATE public.%s SET id=11,name='B' WHERE id=12" % SAME, 1, 2), "A UPDATE 先 COMMIT，B UPDATE 后 COMMIT", "-- A/node134\nBEGIN; UPDATE public.%s SET id=11,name='A' WHERE id=12; SELECT pg_sleep(1); COMMIT;\n-- B/node135\nBEGIN; UPDATE public.%s SET id=11,name='B' WHERE id=12; SELECT pg_sleep(2); COMMIT" % (SAME,SAME), 35),
        wait_for("等待场景一 A 记录 update_pkey_exists", A_PORT, "SELECT count(*) FROM fdd.mmr_conflict_history WHERE relname='%s' AND conflict_type='update_pkey_exists'" % SAME, "1"),
        wait_for("等待场景一 B 记录 update_pkey_exists", B_PORT, "SELECT count(*) FROM fdd.mmr_conflict_history WHERE relname='%s' AND conflict_type='update_pkey_exists'" % SAME, "1"),
        history("展示场景一 A 完整冲突记录", A_PORT, SAME, "返回 update_pkey_exists、update_if_newer 和 remote", "update_pkey_exists", "update_if_newer", "remote"),
        history("展示场景一 B 完整冲突记录", B_PORT, SAME, "返回 update_pkey_exists、update_if_newer 和 local", "update_pkey_exists", "update_if_newer", "local"),
        data("展示场景一 A 最终数据", A_PORT, SAME, "返回 id=11,name=B", "11", "B"), data("展示场景一 B 最终数据", B_PORT, SAME, "返回 id=11,name=B", "11", "B"),
        data("展示场景二触发前 A 初始数据", A_PORT, DIFF, "返回 id=11,name=B", "11", "B"), data("展示场景二触发前 B 初始数据", B_PORT, DIFF, "返回 id=11,name=B", "11", "B"),
        query("确认场景二触发前没有旧冲突记录", A_PORT, "SELECT count(*) AS conflict_count FROM fdd.mmr_conflict_history WHERE relname='%s'" % DIFF, "返回 0", "0"),
        sql("场景二并发更新为不同主键，A 先提交 B 后提交", parallel_transactions(PSQL, A_PORT, "UPDATE public.%s SET id=16 WHERE id=11" % DIFF, B_PORT, "UPDATE public.%s SET id=15 WHERE id=11" % DIFF, 1, 2), "A 更新 id=16 先 COMMIT；B 更新 id=15 后 COMMIT", "-- A/node134\nBEGIN; UPDATE public.%s SET id=16 WHERE id=11; SELECT pg_sleep(1); COMMIT;\n-- B/node135\nBEGIN; UPDATE public.%s SET id=15 WHERE id=11; SELECT pg_sleep(2); COMMIT" % (DIFF,DIFF), 35),
        wait_for("等待场景二 A 记录 update_missing", A_PORT, "SELECT count(*) FROM fdd.mmr_conflict_history WHERE relname='%s' AND conflict_type='update_missing'" % DIFF, "1"), wait_for("等待场景二 B 记录 update_missing", B_PORT, "SELECT count(*) FROM fdd.mmr_conflict_history WHERE relname='%s' AND conflict_type='update_missing'" % DIFF, "1"),
        history("展示场景二 A 完整冲突记录", A_PORT, DIFF, "返回 update_missing、insert_or_skip 和 remote", "update_missing", "insert_or_skip", "remote"), history("展示场景二 B 完整冲突记录", B_PORT, DIFF, "返回 update_missing、insert_or_skip 和 remote", "update_missing", "insert_or_skip", "remote"),
        data("展示场景二 A 最终数据", A_PORT, DIFF, "返回 id=15、16，name 均为 B", "15", "16", "B"), data("展示场景二 B 最终数据", B_PORT, DIFF, "返回 id=15、16，name 均为 B", "15", "16", "B"),
        data("展示场景三触发前 A 初始数据", A_PORT, VALUE, "返回 id=2,name=bnbb", "2", "bnbb"), data("展示场景三触发前 B 初始数据", B_PORT, VALUE, "返回 id=2,name=bnbb", "2", "bnbb"),
        query("确认场景三触发前没有旧冲突记录", A_PORT, "SELECT count(*) AS conflict_count FROM fdd.mmr_conflict_history WHERE relname='%s'" % VALUE, "返回 0", "0"),
        sql("场景三并发更新非主键列，A 后于 B 提交", parallel_transactions(PSQL, A_PORT, "UPDATE public.%s SET name='A-new' WHERE id=2" % VALUE, B_PORT, "UPDATE public.%s SET name='B-new' WHERE id=2" % VALUE, 2, 1), "B UPDATE 先 COMMIT；A UPDATE 后 COMMIT", "-- A/node134\nBEGIN; UPDATE public.%s SET name='A-new' WHERE id=2; SELECT pg_sleep(2); COMMIT;\n-- B/node135\nBEGIN; UPDATE public.%s SET name='B-new' WHERE id=2; SELECT pg_sleep(1); COMMIT" % (VALUE,VALUE), 35),
        wait_for("等待场景三 A 记录 update_origin_change", A_PORT, "SELECT count(*) FROM fdd.mmr_conflict_history WHERE relname='%s' AND conflict_type='update_origin_change'" % VALUE, "1"), wait_for("等待场景三 B 记录 update_origin_change", B_PORT, "SELECT count(*) FROM fdd.mmr_conflict_history WHERE relname='%s' AND conflict_type='update_origin_change'" % VALUE, "1"),
        history("展示场景三 A 完整冲突记录", A_PORT, VALUE, "返回 update_origin_change、update_if_newer 和 local", "update_origin_change", "update_if_newer", "local"), history("展示场景三 B 完整冲突记录", B_PORT, VALUE, "返回 update_origin_change、update_if_newer 和 remote", "update_origin_change", "update_if_newer", "remote"),
        data("展示场景三 A 最终数据", A_PORT, VALUE, "返回 id=2,name=A-new", "2", "A-new"), data("展示场景三 B 最终数据", B_PORT, VALUE, "返回 id=2,name=A-new", "2", "A-new"),
    ],
    "teardown": "以 immediate 停止临时 A/node134 和 B/node135 PostgreSQL 实例；递归删除临时数据目录，连同三张 UPDATE-UPDATE 测试表、MMR 元数据、订阅、复制槽和本轮冲突记录一并删除。",
}
