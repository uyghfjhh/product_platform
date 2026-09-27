from suites.mmr.cases.conflict.delete_missing import query, setup, sql
from suites.mmr.cases.node_management.create_group import (PSQL, create_node,
                                                            init_instance, psql, shell)
from suites.mmr.conflict_timing import parallel_transactions


ROOT = "/tmp/fbase_regress_mmr_conflict_identity_full_pk_{run_id}"
A, B = ROOT + "/a", ROOT + "/b"
A_PORT, B_PORT = "15515", "15516"
DSN = "host=127.0.0.1 port=%s user=postgres dbname=postgres" % A_PORT
SAME, DIFF, VALUE = ("identity_full_pk_%s_{run_id}" % n for n in ("same", "diff", "value"))


def wait_for(title, port, statement, expected, timeout=35):
    return sql(title, "for i in $(seq 1 30); do v=$(%s -X -At -h 127.0.0.1 -p %s -U postgres -d postgres -c %r); test \"$v\" = %r && { echo \"$v\"; exit 0; }; sleep 1; done; exit 1" % (PSQL, port, statement, expected), "30 秒内输出 %s" % expected, statement, timeout)


def rows(title, port, table, expected, *markers):
    return query(title, port, "SELECT id,name,age,city FROM public.%s ORDER BY id" % table, expected, *markers)


def history(title, port, table, expected, *markers):
    return query(title, port, "SELECT conflict_type,conflict_resolution,relname,apply_tuple FROM fdd.mmr_conflict_history WHERE relname='%s' ORDER BY local_lsn" % table, expected, *markers)


def before(label, table, *markers):
    return [rows("展示%s触发前 A 初始数据" % label, A_PORT, table, "展示文档规定初始数据", *markers), rows("展示%s触发前 B 初始数据" % label, B_PORT, table, "展示文档规定初始数据", *markers), query("确认%s触发前 A 没有旧冲突记录" % label, A_PORT, "SELECT count(*) AS conflict_count FROM fdd.mmr_conflict_history WHERE relname='%s'" % table, "返回 0", "0"), query("确认%s触发前 B 没有旧冲突记录" % label, B_PORT, "SELECT count(*) AS conflict_count FROM fdd.mmr_conflict_history WHERE relname='%s'" % table, "返回 0", "0")]


def race(title, a, b, expected, display):
    return sql(title, parallel_transactions(PSQL, A_PORT, a, B_PORT, b, 1, 2), expected, display, 35)


DDL = "; ".join(["CREATE TABLE public.%s(id int PRIMARY KEY,name text,age text,city text); ALTER TABLE public.%s REPLICA IDENTITY FULL" % (t,t) for t in (SAME,DIFF,VALUE)] + ["INSERT INTO public.%s VALUES%s" % item for item in [(SAME,"(2,'judy','22','A')"),(DIFF,"(4,'B','22','A')"),(VALUE,"(2,'judy','22','A')")]])


CASE = {
    "id": "mmr.conflict.update_update_identity_full_pk", "name": "REPLICA IDENTITY FULL 有主键表的 UPDATE-UPDATE 冲突",
    "document": "多活功能测试文档.md", "section": "4.3.3.2", "group": "conflict",
    "fixtures": ["cluster", {"type": "isolated_mmr_node_creation", "data_dir": ROOT}],
    "requirements": {"clusters": ["mmr"], "plugins": ["fdd_mmr"], "writable_node": True, "node": "mmr:mmr1"}, "evidence_nodes": ["mmr:mmr1"],
    "prerequisites": ["隔离 A/node134、B/node135 采用 all 模式组成两节点组；三张有主键表均启用 REPLICA IDENTITY FULL。", "按文档提交次序 A 先、B 后执行三种 UPDATE-UPDATE 场景。"],
    "steps": [
        setup(init_instance("初始化 A/node134", A, A_PORT)), setup(create_node(A_PORT, "node134")), setup(shell("创建三张 FULL 复制标识主键表和等价初始数据", psql(A_PORT, DDL), "返回 CREATE TABLE、ALTER TABLE 和三次 INSERT 0 1")), setup(shell("创建 g1 集群", psql(A_PORT, "SELECT fdd.create_group('g1')"), "返回 node group create successful")), setup(init_instance("初始化 B/node135", B, B_PORT)), setup(create_node(B_PORT, "node135")), setup(shell("以 all 模式将 B 加入 g1", psql(B_PORT, "SELECT fdd.join_group('g1','%s',true,'all','table_exist_error')" % DSN), "返回 node join to group complete finished", timeout=90)),
        query("读取 A/node134 实际 debug_logical_replication_streaming", A_PORT, "SHOW debug_logical_replication_streaming", "返回 buffered", "buffered"), query("读取 B/node135 实际 debug_logical_replication_streaming", B_PORT, "SHOW debug_logical_replication_streaming", "返回 buffered", "buffered"), query("读取 A/node134 实际 logical_decoding_work_mem", A_PORT, "SHOW logical_decoding_work_mem", "返回 64MB", "64MB"), query("读取 B/node135 实际 logical_decoding_work_mem", B_PORT, "SHOW logical_decoding_work_mem", "返回 64MB", "64MB"), query("读取 A/node134 的多活 streaming 模式", A_PORT, "SELECT CASE streaming WHEN 'f' THEN 'off' WHEN 't' THEN 'on' WHEN 'p' THEN 'parallel' END FROM fdd.mmr_node WHERE node_name='node134'", "返回 off", "off"), query("读取 B/node135 的多活 streaming 模式", B_PORT, "SELECT CASE streaming WHEN 'f' THEN 'off' WHEN 't' THEN 'on' WHEN 'p' THEN 'parallel' END FROM fdd.mmr_node WHERE node_name='node135'", "返回 off", "off"),
    ] + before("场景一：同目标主键", SAME, "2", "judy", "22", "A", "(1 row)") + [
        race("场景一并发更新为 id=4，B 后提交", "UPDATE public.%s SET id=4,name='A' WHERE id=2" % SAME, "UPDATE public.%s SET id=4,name='B' WHERE id=2" % SAME, "A/B 均 UPDATE 1，A 先 COMMIT、B 后 COMMIT", "-- A/node134\nBEGIN; UPDATE public.%s SET id=4,name='A' WHERE id=2; SELECT pg_sleep(1); COMMIT;\n-- B/node135\nBEGIN; UPDATE public.%s SET id=4,name='B' WHERE id=2; SELECT pg_sleep(2); COMMIT" % (SAME,SAME)),
        wait_for("等待场景一 A 记录 update_pkey_exists", A_PORT, "SELECT count(*) FROM fdd.mmr_conflict_history WHERE relname='%s' AND conflict_type='update_pkey_exists'" % SAME, "1"), wait_for("等待场景一 B 记录 update_pkey_exists", B_PORT, "SELECT count(*) FROM fdd.mmr_conflict_history WHERE relname='%s' AND conflict_type='update_pkey_exists'" % SAME, "1"), history("展示场景一 A 完整冲突记录", A_PORT, SAME, "返回 update_pkey_exists、update_if_newer、remote", "update_pkey_exists", "update_if_newer", "remote"), history("展示场景一 B 完整冲突记录", B_PORT, SAME, "返回 update_pkey_exists、update_if_newer、local", "update_pkey_exists", "update_if_newer", "local"), rows("展示场景一 A 最终数据", A_PORT, SAME, "返回 id=4,B", "4", "B", "(1 row)"), rows("展示场景一 B 最终数据", B_PORT, SAME, "返回 id=4,B", "4", "B", "(1 row)"),
    ] + before("场景二：不同目标主键", DIFF, "4", "B", "22", "A", "(1 row)") + [
        race("场景二并发更新为 id=6/8，B 后提交", "UPDATE public.%s SET id=6,name='A_new' WHERE id=4" % DIFF, "UPDATE public.%s SET id=8,name='B_new' WHERE id=4" % DIFF, "A/B 均 UPDATE 1，A 先 COMMIT、B 后 COMMIT", "-- A/node134\nBEGIN; UPDATE public.%s SET id=6,name='A_new' WHERE id=4; SELECT pg_sleep(1); COMMIT;\n-- B/node135\nBEGIN; UPDATE public.%s SET id=8,name='B_new' WHERE id=4; SELECT pg_sleep(2); COMMIT" % (DIFF,DIFF)),
        wait_for("等待场景二 A 记录 update_missing", A_PORT, "SELECT count(*) FROM fdd.mmr_conflict_history WHERE relname='%s' AND conflict_type='update_missing'" % DIFF, "1"), wait_for("等待场景二 B 记录 update_missing", B_PORT, "SELECT count(*) FROM fdd.mmr_conflict_history WHERE relname='%s' AND conflict_type='update_missing'" % DIFF, "1"), history("展示场景二 A 完整冲突记录", A_PORT, DIFF, "返回 update_missing、insert_or_skip、remote", "update_missing", "insert_or_skip", "remote"), history("展示场景二 B 完整冲突记录", B_PORT, DIFF, "返回 update_missing、insert_or_skip、remote", "update_missing", "insert_or_skip", "remote"), rows("展示场景二 A 最终数据", A_PORT, DIFF, "返回 id=6,A_new 和 id=8,B_new", "6", "8", "A_new", "B_new", "(2 rows)"), rows("展示场景二 B 最终数据", B_PORT, DIFF, "返回 id=6,A_new 和 id=8,B_new", "6", "8", "A_new", "B_new", "(2 rows)"),
    ] + before("场景三：不更新主键", VALUE, "2", "judy", "22", "A", "(1 row)") + [
        race("场景三并发更新 name，B 后提交", "UPDATE public.%s SET name='A_new' WHERE id=2" % VALUE, "UPDATE public.%s SET name='B_new' WHERE id=2" % VALUE, "A/B 均 UPDATE 1，A 先 COMMIT、B 后 COMMIT", "-- A/node134\nBEGIN; UPDATE public.%s SET name='A_new' WHERE id=2; SELECT pg_sleep(1); COMMIT;\n-- B/node135\nBEGIN; UPDATE public.%s SET name='B_new' WHERE id=2; SELECT pg_sleep(2); COMMIT" % (VALUE,VALUE)),
        wait_for("等待场景三 A 记录 update_origin_change", A_PORT, "SELECT count(*) FROM fdd.mmr_conflict_history WHERE relname='%s' AND conflict_type='update_origin_change'" % VALUE, "1"), wait_for("等待场景三 B 记录 update_origin_change", B_PORT, "SELECT count(*) FROM fdd.mmr_conflict_history WHERE relname='%s' AND conflict_type='update_origin_change'" % VALUE, "1"), history("展示场景三 A 完整冲突记录", A_PORT, VALUE, "返回 update_origin_change、update_if_newer、remote", "update_origin_change", "update_if_newer", "remote"), history("展示场景三 B 完整冲突记录", B_PORT, VALUE, "返回 update_origin_change、update_if_newer、local", "update_origin_change", "update_if_newer", "local"), rows("展示场景三 A 最终数据", A_PORT, VALUE, "返回 id=2,B_new", "2", "B_new", "(1 row)"), rows("展示场景三 B 最终数据", B_PORT, VALUE, "返回 id=2,B_new", "2", "B_new", "(1 row)"),
    ],
    "teardown": "以 immediate 停止临时 A/node134 和 B/node135 PostgreSQL 实例；递归删除临时数据目录，连同三张 FULL 复制标识主键表、MMR 元数据、订阅、复制槽和本轮冲突记录一并删除。",
}
