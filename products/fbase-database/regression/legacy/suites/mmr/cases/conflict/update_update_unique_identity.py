from suites.mmr.cases.conflict.delete_missing import query, setup, sql
from suites.mmr.cases.node_management.create_group import (PSQL, create_node,
                                                            init_instance, psql, shell)
from suites.mmr.conflict_timing import parallel_transactions


ROOT = "/tmp/fbase_regress_mmr_conflict_update_update_unique_{run_id}"
A, B = ROOT + "/a", ROOT + "/b"
A_PORT, B_PORT = "15511", "15512"
DSN = "host=127.0.0.1 port=%s user=postgres dbname=postgres" % A_PORT
N1, N2, N3 = ("unique_nopk_%s_{run_id}" % n for n in ("one", "two", "three"))
P1, P2 = ("unique_pk_%s_{run_id}" % n for n in ("one", "two"))


def wait_for(title, port, statement, expected, timeout=35):
    return sql(title,
               "for i in $(seq 1 30); do v=$(%s -X -At -h 127.0.0.1 -p %s -U postgres -d postgres -c %r); test \"$v\" = %r && { echo \"$v\"; exit 0; }; sleep 1; done; exit 1" %
               (PSQL, port, statement, expected), "30 秒内输出 %s" % expected, statement, timeout)


def rows(title, port, table, expected, *markers):
    return query(title, port, "SELECT id,name,age,city FROM public.%s ORDER BY name,id" % table,
                 expected, *markers)


def history(title, port, table, expected, *markers):
    return query(title, port,
                 "SELECT conflict_type,conflict_resolution,relname,apply_tuple FROM fdd.mmr_conflict_history WHERE relname='%s' ORDER BY local_lsn" % table,
                 expected, *markers)


def base(label, table, initial_markers):
    return [
        rows("展示%s触发前 A/node134 初始数据" % label, A_PORT, table,
             "展示文档规定的初始数据", *initial_markers),
        rows("展示%s触发前 B/node135 初始数据" % label, B_PORT, table,
             "展示文档规定的初始数据", *initial_markers),
        query("确认%s触发前 A 没有该表旧冲突记录" % label, A_PORT,
              "SELECT count(*) AS conflict_count FROM fdd.mmr_conflict_history WHERE relname='%s'" % table,
              "返回 0", "0"),
        query("确认%s触发前 B 没有该表旧冲突记录" % label, B_PORT,
              "SELECT count(*) AS conflict_count FROM fdd.mmr_conflict_history WHERE relname='%s'" % table,
              "返回 0", "0"),
    ]


def race(label, table, a_statement, b_statement, expected, display):
    return sql(label, parallel_transactions(PSQL, A_PORT, a_statement, B_PORT,
                                             b_statement, 2, 1), expected, display, 35)


NO_PK_DDL = "(id int,name text NOT NULL,age text NOT NULL,city text)"
PK_DDL = "(id int PRIMARY KEY,name text NOT NULL,age text NOT NULL,city text)"
DDL = "; ".join(
    ["CREATE TABLE public.%s%s" % (table, NO_PK_DDL) for table in (N1, N2, N3)] +
    ["CREATE TABLE public.%s%s" % (table, PK_DDL) for table in (P1, P2)] +
    ["CREATE UNIQUE INDEX %s_idx ON public.%s(name,age); ALTER TABLE public.%s REPLICA IDENTITY USING INDEX %s_idx" % (table, table, table, table) for table in (N1, N2, N3, P1, P2)] +
    ["INSERT INTO public.%s VALUES%s" % pair for pair in [
        (N1, "(1,'boby','22','cs')"), (N2, "(2,'boby','22','A')"),
        (N3, "(2,'judy','22','A_New_new')"), (P1, "(2,'boby','22','cs')"),
        (P2, "(7,'boby','22','A')")]])


CASE = {
    "id": "mmr.conflict.update_update_unique_identity",
    "name": "唯一索引复制标识的 UPDATE-UPDATE 冲突",
    "document": "多活功能测试文档.md", "section": "4.3.2.2", "group": "conflict",
    "fixtures": ["cluster", {"type": "isolated_mmr_node_creation", "data_dir": ROOT}],
    "requirements": {"clusters": ["mmr"], "plugins": ["fdd_mmr"], "writable_node": True, "node": "mmr:mmr1"},
    "evidence_nodes": ["mmr:mmr1"],
    "prerequisites": ["文档标题误写为 4.3.1.2；本用例按覆盖清单对应 4.3.2.2。五张临时表分别隔离无主键三种场景和有主键两种场景。", "并发事务均让 A 后于 B 提交，以复现文档的 local/remote 处理方向。"],
    "steps": [
        setup(init_instance("初始化 A/node134", A, A_PORT)), setup(create_node(A_PORT, "node134")),
        setup(shell("创建唯一索引复制标识测试表、索引和初始数据", psql(A_PORT, DDL), "返回 CREATE TABLE、CREATE INDEX、ALTER TABLE 和五次 INSERT 0 1")),
        setup(shell("创建 g1 集群", psql(A_PORT, "SELECT fdd.create_group('g1')"), "返回 node group create successful")),
        setup(init_instance("初始化 B/node135", B, B_PORT)), setup(create_node(B_PORT, "node135")),
        setup(shell("以 all 模式将 B 加入 g1", psql(B_PORT, "SELECT fdd.join_group('g1','%s',true,'all','table_exist_error')" % DSN), "返回 node join to group complete finished", timeout=90)),
        query("读取 A/node134 实际 debug_logical_replication_streaming", A_PORT, "SHOW debug_logical_replication_streaming", "返回 buffered", "buffered"),
        query("读取 B/node135 实际 debug_logical_replication_streaming", B_PORT, "SHOW debug_logical_replication_streaming", "返回 buffered", "buffered"),
        query("读取 A/node134 实际 logical_decoding_work_mem", A_PORT, "SHOW logical_decoding_work_mem", "返回 64MB", "64MB"),
        query("读取 B/node135 实际 logical_decoding_work_mem", B_PORT, "SHOW logical_decoding_work_mem", "返回 64MB", "64MB"),
        query("读取 A/node134 的多活 streaming 模式", A_PORT, "SELECT CASE streaming WHEN 'f' THEN 'off' WHEN 't' THEN 'on' WHEN 'p' THEN 'parallel' END FROM fdd.mmr_node WHERE node_name='node134'", "返回 off", "off"),
        query("读取 B/node135 的多活 streaming 模式", B_PORT, "SELECT CASE streaming WHEN 'f' THEN 'off' WHEN 't' THEN 'on' WHEN 'p' THEN 'parallel' END FROM fdd.mmr_node WHERE node_name='node135'", "返回 off", "off"),
    ] + base("无主键场景一：仅改非复制标识列", N1, ("1", "boby", "22", "cs", "(1 row)")) + [
        race("无主键场景一并发修改 id/city，A 后提交", N1,
             "UPDATE public.%s SET id=2,city='A' WHERE id=1" % N1,
             "UPDATE public.%s SET id=2,city='B' WHERE id=1" % N1,
             "A/B 均 UPDATE 1，B 先 COMMIT、A 后 COMMIT",
             "-- A/node134\nBEGIN; UPDATE public.%s SET id=2,city='A' WHERE id=1; SELECT pg_sleep(2); COMMIT;\n-- B/node135\nBEGIN; UPDATE public.%s SET id=2,city='B' WHERE id=1; SELECT pg_sleep(1); COMMIT" % (N1,N1)),
        wait_for("等待无主键场景一 A 记录 update_origin_change", A_PORT, "SELECT count(*) FROM fdd.mmr_conflict_history WHERE relname='%s' AND conflict_type='update_origin_change'" % N1, "1"), wait_for("等待无主键场景一 B 记录 update_origin_change", B_PORT, "SELECT count(*) FROM fdd.mmr_conflict_history WHERE relname='%s' AND conflict_type='update_origin_change'" % N1, "1"),
        history("展示无主键场景一 A 完整冲突记录", A_PORT, N1, "返回 update_origin_change、update_if_newer、local", "update_origin_change", "update_if_newer", "local"), history("展示无主键场景一 B 完整冲突记录", B_PORT, N1, "返回 update_origin_change、update_if_newer、remote", "update_origin_change", "update_if_newer", "remote"),
        rows("展示无主键场景一 A 最终数据", A_PORT, N1, "返回 id=2,boby,22,A", "2", "boby", "22", "A", "(1 row)"), rows("展示无主键场景一 B 最终数据", B_PORT, N1, "返回 id=2,boby,22,A", "2", "boby", "22", "A", "(1 row)"),
    ] + base("无主键场景二：复制标识改为相同值", N2, ("2", "boby", "22", "A", "(1 row)")) + [
        race("无主键场景二并发改 name=judy，A 后提交", N2,
             "UPDATE public.%s SET name='judy',city='A_New_new' WHERE id=2" % N2,
             "UPDATE public.%s SET name='judy',city='B_New_new' WHERE id=2" % N2,
             "A/B 均 UPDATE 1，B 先 COMMIT、A 后 COMMIT",
             "-- A/node134\nBEGIN; UPDATE public.%s SET name='judy',city='A_New_new' WHERE id=2; SELECT pg_sleep(2); COMMIT;\n-- B/node135\nBEGIN; UPDATE public.%s SET name='judy',city='B_New_new' WHERE id=2; SELECT pg_sleep(1); COMMIT" % (N2,N2)),
        wait_for("等待无主键场景二 A 记录 update_origin_change", A_PORT, "SELECT count(*) FROM fdd.mmr_conflict_history WHERE relname='%s' AND conflict_type='update_origin_change'" % N2, "1"), wait_for("等待无主键场景二 B 记录 update_origin_change", B_PORT, "SELECT count(*) FROM fdd.mmr_conflict_history WHERE relname='%s' AND conflict_type='update_origin_change'" % N2, "1"),
        history("展示无主键场景二 A 完整冲突记录", A_PORT, N2, "返回 update_origin_change、update_if_newer、local", "update_origin_change", "update_if_newer", "local"), history("展示无主键场景二 B 完整冲突记录", B_PORT, N2, "返回 update_origin_change、update_if_newer、remote", "update_origin_change", "update_if_newer", "remote"),
        rows("展示无主键场景二 A 最终数据", A_PORT, N2, "返回 id=2,judy,22,A_New_new", "2", "judy", "22", "A_New_new", "(1 row)"), rows("展示无主键场景二 B 最终数据", B_PORT, N2, "返回 id=2,judy,22,A_New_new", "2", "judy", "22", "A_New_new", "(1 row)"),
    ] + base("无主键场景三：复制标识改为不同值", N3, ("2", "judy", "22", "A_New_new", "(1 row)")) + [
        race("无主键场景三并发改为不同 name，A 后提交", N3,
             "UPDATE public.%s SET name='A',city='A_diff' WHERE id=2" % N3,
             "UPDATE public.%s SET name='B',city='B_New_diff' WHERE id=2" % N3,
             "A/B 均 UPDATE 1，B 先 COMMIT、A 后 COMMIT",
             "-- A/node134\nBEGIN; UPDATE public.%s SET name='A',city='A_diff' WHERE id=2; SELECT pg_sleep(2); COMMIT;\n-- B/node135\nBEGIN; UPDATE public.%s SET name='B',city='B_New_diff' WHERE id=2; SELECT pg_sleep(1); COMMIT" % (N3,N3)),
        wait_for("等待无主键场景三 A 记录 update_missing", A_PORT, "SELECT count(*) FROM fdd.mmr_conflict_history WHERE relname='%s' AND conflict_type='update_missing'" % N3, "1"), wait_for("等待无主键场景三 B 记录 update_missing", B_PORT, "SELECT count(*) FROM fdd.mmr_conflict_history WHERE relname='%s' AND conflict_type='update_missing'" % N3, "1"),
        history("展示无主键场景三 A 完整冲突记录", A_PORT, N3, "返回 update_missing、insert_or_skip、remote", "update_missing", "insert_or_skip", "remote"), history("展示无主键场景三 B 完整冲突记录", B_PORT, N3, "返回 update_missing、insert_or_skip、remote", "update_missing", "insert_or_skip", "remote"),
        rows("展示无主键场景三 A 最终数据", A_PORT, N3, "返回 A 和 B 两行", "A", "B", "A_diff", "B_New_diff", "(2 rows)"), rows("展示无主键场景三 B 最终数据", B_PORT, N3, "返回 A 和 B 两行", "A", "B", "A_diff", "B_New_diff", "(2 rows)"),
    ] + base("有主键场景一：仅改主键和其他列", P1, ("2", "boby", "22", "cs", "(1 row)")) + [
        race("有主键场景一并发改 id=7/city，A 后提交", P1,
             "UPDATE public.%s SET id=7,city='A' WHERE id=2" % P1,
             "UPDATE public.%s SET id=7,city='B' WHERE id=2" % P1,
             "A/B 均 UPDATE 1，B 先 COMMIT、A 后 COMMIT",
             "-- A/node134\nBEGIN; UPDATE public.%s SET id=7,city='A' WHERE id=2; SELECT pg_sleep(2); COMMIT;\n-- B/node135\nBEGIN; UPDATE public.%s SET id=7,city='B' WHERE id=2; SELECT pg_sleep(1); COMMIT" % (P1,P1)),
        wait_for("等待有主键场景一 A 记录 update_origin_change", A_PORT, "SELECT count(*) FROM fdd.mmr_conflict_history WHERE relname='%s' AND conflict_type='update_origin_change'" % P1, "1"), wait_for("等待有主键场景一 B 记录 update_origin_change", B_PORT, "SELECT count(*) FROM fdd.mmr_conflict_history WHERE relname='%s' AND conflict_type='update_origin_change'" % P1, "1"),
        history("展示有主键场景一 A 完整冲突记录", A_PORT, P1, "返回 update_origin_change、update_if_newer、local", "update_origin_change", "update_if_newer", "local"), history("展示有主键场景一 B 完整冲突记录", B_PORT, P1, "返回 update_origin_change、update_if_newer、remote", "update_origin_change", "update_if_newer", "remote"),
        rows("展示有主键场景一 A 最终数据", A_PORT, P1, "返回 id=7,boby,22,A", "7", "boby", "22", "A", "(1 row)"), rows("展示有主键场景一 B 最终数据", B_PORT, P1, "返回 id=7,boby,22,A", "7", "boby", "22", "A", "(1 row)"),
    ] + base("有主键场景二：同时修改主键和复制标识", P2, ("7", "boby", "22", "A", "(1 row)")) + [
        race("有主键场景二并发修改不同主键和 name，A 后提交", P2,
             "UPDATE public.%s SET id=9,name='A_new' WHERE id=7" % P2,
             "UPDATE public.%s SET id=10,name='b_new' WHERE id=7" % P2,
             "A/B 均 UPDATE 1，B 先 COMMIT、A 后 COMMIT",
             "-- A/node134\nBEGIN; UPDATE public.%s SET id=9,name='A_new' WHERE id=7; SELECT pg_sleep(2); COMMIT;\n-- B/node135\nBEGIN; UPDATE public.%s SET id=10,name='b_new' WHERE id=7; SELECT pg_sleep(1); COMMIT" % (P2,P2)),
        wait_for("等待有主键场景二 A 记录 update_missing", A_PORT, "SELECT count(*) FROM fdd.mmr_conflict_history WHERE relname='%s' AND conflict_type='update_missing'" % P2, "1"), wait_for("等待有主键场景二 B 记录 update_missing", B_PORT, "SELECT count(*) FROM fdd.mmr_conflict_history WHERE relname='%s' AND conflict_type='update_missing'" % P2, "1"),
        history("展示有主键场景二 A 完整冲突记录", A_PORT, P2, "返回 update_missing、insert_or_skip、remote", "update_missing", "insert_or_skip", "remote"), history("展示有主键场景二 B 完整冲突记录", B_PORT, P2, "返回 update_missing、insert_or_skip、remote", "update_missing", "insert_or_skip", "remote"),
        rows("展示有主键场景二 A 最终数据", A_PORT, P2, "返回 id=9,A_new 和 id=10,b_new", "9", "10", "A_new", "b_new", "(2 rows)"), rows("展示有主键场景二 B 最终数据", B_PORT, P2, "返回 id=9,A_new 和 id=10,b_new", "9", "10", "A_new", "b_new", "(2 rows)"),
    ],
    "teardown": "以 immediate 停止临时 A/node134 和 B/node135 PostgreSQL 实例；递归删除临时数据目录，连同五张唯一索引复制标识测试表、索引、MMR 元数据、订阅、复制槽和本轮冲突记录一并删除。",
}
