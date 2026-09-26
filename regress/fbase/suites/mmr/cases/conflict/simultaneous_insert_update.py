from suites.mmr.cases.conflict.delete_missing import query, setup, sql
from suites.mmr.cases.node_management.create_group import (PSQL, create_node,
                                                            init_instance, psql, shell)
from suites.mmr.conflict_timing import parallel_transactions


ROOT = "/tmp/fbase_regress_mmr_conflict_simultaneous_insert_update_{run_id}"
A, B = ROOT + "/a", ROOT + "/b"
A_PORT, B_PORT = "15507", "15508"
DSN = "host=127.0.0.1 port=%s user=postgres dbname=postgres" % A_PORT
TABLES = {
    "one": "sim_insert_update_one_{run_id}",
    "two": "sim_insert_update_two_{run_id}",
    "three": "sim_insert_update_three_{run_id}",
    "four": "sim_insert_update_four_{run_id}",
}


def wait_for(title, port, statement, expected, timeout=35):
    return sql(
        title,
        "for i in $(seq 1 30); do v=$(%s -X -At -h 127.0.0.1 -p %s -U postgres -d postgres -c %r); "
        "test \"$v\" = %s && { echo \"$v\"; exit 0; }; sleep 1; done; exit 1" %
        (PSQL, port, statement, repr(expected)),
        "30 秒内输出 %s" % expected, statement, timeout)


def conflict_history(title, port, table, expected, *markers):
    return query(
        title, port,
        "SELECT conflict_type,conflict_resolution,relname,apply_tuple "
        "FROM fdd.mmr_conflict_history WHERE relname='%s' ORDER BY local_lsn" % table,
        expected, *markers)


def final_data(title, port, table, expected, *markers):
    return query(title, port, "SELECT id,name FROM public.%s ORDER BY id" % table,
                 expected, *markers)


def document_evidence(step):
    return step


def initial_data(label, table):
    return [
        query("展示场景%s触发前 A/node134 的初始数据" % label, A_PORT,
              "SELECT id,name FROM public.%s ORDER BY id" % table,
              "返回 id=11,name=jone", "11", "jone"),
        query("展示场景%s触发前 B/node135 的初始数据" % label, B_PORT,
              "SELECT id,name FROM public.%s ORDER BY id" % table,
              "返回 id=11,name=jone", "11", "jone"),
        query("确认场景%s触发前 A 没有该表的冲突记录" % label, A_PORT,
              "SELECT count(*) AS conflict_count FROM fdd.mmr_conflict_history WHERE relname='%s'" % table,
              "返回 0", "0"),
        query("确认场景%s触发前 B 没有该表的冲突记录" % label, B_PORT,
              "SELECT count(*) AS conflict_count FROM fdd.mmr_conflict_history WHERE relname='%s'" % table,
              "返回 0", "0"),
    ]


T1, T2, T3, T4 = (TABLES[key] for key in ("one", "two", "three", "four"))
CREATE_TABLES = "; ".join(
    "CREATE TABLE public.%s(id int PRIMARY KEY,name varchar)" % table
    for table in (T1, T2, T3, T4))


CASE = {
    "id": "mmr.conflict.simultaneous_insert_update",
    "name": "插入和更新同时进行的四种初始归属场景",
    "document": "多活功能测试文档.md", "section": "4.3.1.3", "group": "conflict",
    "fixtures": ["cluster", {"type": "isolated_mmr_node_creation", "data_dir": ROOT}],
    "requirements": {"clusters": ["mmr"], "plugins": ["fdd_mmr"],
                     "writable_node": True, "node": "mmr:mmr1"},
    "evidence_nodes": ["mmr:mmr1"],
    "prerequisites": [
        "隔离 A/node134、B/node135 采用 all 模式组成两节点多活组；四张表分别隔离文档 4.3.1.3 的四个场景。",
        "提交顺序按文档各场景给出的时间关系受控：场景一、三 A 后于 B；场景二、四 A 先于 B。",
    ],
    "steps": [
        setup(init_instance("初始化 A/node134", A, A_PORT)),
        setup(create_node(A_PORT, "node134")),
        setup(shell("创建四张插入更新冲突测试表", psql(A_PORT, CREATE_TABLES),
                    "返回四次 CREATE TABLE")),
        setup(shell("在 A 写入场景一和场景二的初始 id=11", psql(
            A_PORT, "INSERT INTO public.%s VALUES(11,'jone'); INSERT INTO public.%s VALUES(11,'jone')" %
            (T1, T2)), "返回两次 INSERT 0 1")),
        setup(shell("创建 g1 集群", psql(A_PORT, "SELECT fdd.create_group('g1')"),
                    "返回 node group create successful")),
        setup(init_instance("初始化 B/node135", B, B_PORT)),
        setup(create_node(B_PORT, "node135")),
        setup(shell("以 all 模式将 B 加入 g1", psql(
            B_PORT, "SELECT fdd.join_group('g1','%s',true,'all','table_exist_error')" % DSN),
            "返回 node join to group complete finished", timeout=90)),
        setup(shell("在 B 写入场景三和场景四的初始 id=11", psql(
            B_PORT, "INSERT INTO public.%s VALUES(11,'jone'); INSERT INTO public.%s VALUES(11,'jone')" %
            (T3, T4)), "返回两次 INSERT 0 1")),
        setup(wait_for("等待 A 接收 B 写入的场景三初始数据", A_PORT,
                       "SELECT name FROM public.%s WHERE id=11" % T3, "jone")),
        setup(wait_for("等待 A 接收 B 写入的场景四初始数据", A_PORT,
                       "SELECT name FROM public.%s WHERE id=11" % T4, "jone")),
        query("读取 A/node134 实际 debug_logical_replication_streaming", A_PORT,
              "SHOW debug_logical_replication_streaming", "返回 buffered", "buffered"),
        query("读取 B/node135 实际 debug_logical_replication_streaming", B_PORT,
              "SHOW debug_logical_replication_streaming", "返回 buffered", "buffered"),
        query("读取 A/node134 实际 logical_decoding_work_mem", A_PORT,
              "SHOW logical_decoding_work_mem", "返回 64MB", "64MB"),
        query("读取 B/node135 实际 logical_decoding_work_mem", B_PORT,
              "SHOW logical_decoding_work_mem", "返回 64MB", "64MB"),
        query("读取 A/node134 的多活 streaming 模式", A_PORT,
              "SELECT CASE streaming WHEN 'f' THEN 'off' WHEN 't' THEN 'on' WHEN 'p' THEN 'parallel' END AS streaming_mode FROM fdd.mmr_node WHERE node_name='node134'",
              "返回 off", "off"),
        query("读取 B/node135 的多活 streaming 模式", B_PORT,
              "SELECT CASE streaming WHEN 'f' THEN 'off' WHEN 't' THEN 'on' WHEN 'p' THEN 'parallel' END AS streaming_mode FROM fdd.mmr_node WHERE node_name='node135'",
              "返回 off", "off"),
    ] + initial_data("一", T1) + [
        sql("场景一按文档并发：A 更新、B 插入，A 后于 B 提交",
            parallel_transactions(PSQL, A_PORT, "UPDATE public.%s SET id=33 WHERE id=11" % T1, B_PORT, "INSERT INTO public.%s VALUES(33,'jone_new')" % T1, 2, 1),
            "A 返回 UPDATE 1 且后 COMMIT；B 返回 INSERT 0 1 且先 COMMIT",
            "-- A/node134\nBEGIN; UPDATE public.%s SET id=33 WHERE id=11; SELECT pg_sleep(2); COMMIT;\n-- B/node135\nBEGIN; INSERT INTO public.%s VALUES(33,'jone_new'); SELECT pg_sleep(1); COMMIT" % (T1, T1), 35),
        wait_for("等待场景一 A 记录 insert_exists", A_PORT,
                 "SELECT count(*) FROM fdd.mmr_conflict_history WHERE relname='%s' AND conflict_type='insert_exists'" % T1, "1"),
        wait_for("等待场景一 B 记录 update_pkey_exists", B_PORT,
                 "SELECT count(*) FROM fdd.mmr_conflict_history WHERE relname='%s' AND conflict_type='update_pkey_exists'" % T1, "1"),
        conflict_history("展示场景一 A 的完整冲突记录", A_PORT, T1,
                         "返回 insert_exists、update_if_newer 和 local", "insert_exists", "update_if_newer", "local"),
        conflict_history("展示场景一 B 的完整冲突记录", B_PORT, T1,
                         "返回 update_pkey_exists、update_if_newer 和 remote", "update_pkey_exists", "update_if_newer", "remote"),
        final_data("展示场景一 A 最终数据", A_PORT, T1, "返回 id=33,name=jone", "33", "jone"),
        final_data("展示场景一 B 最终数据", B_PORT, T1, "返回 id=33,name=jone", "33", "jone"),
    ] + initial_data("二", T2) + [
        sql("场景二按文档并发：A 插入、B 更新，A 先于 B 提交",
            parallel_transactions(PSQL, B_PORT, "UPDATE public.%s SET id=33 WHERE id=11" % T2, A_PORT, "INSERT INTO public.%s VALUES(33,'jone_new')" % T2, 2, 1),
            "A 返回 INSERT 0 1 且先 COMMIT；B 返回 UPDATE 1 且后 COMMIT",
            "-- A/node134\nBEGIN; INSERT INTO public.%s VALUES(33,'jone_new'); SELECT pg_sleep(1); COMMIT;\n-- B/node135\nBEGIN; UPDATE public.%s SET id=33 WHERE id=11; SELECT pg_sleep(2); COMMIT" % (T2, T2), 35),
        document_evidence(wait_for("等待场景二 A 记录 update_origin_change", A_PORT,
                 "SELECT count(*) FROM fdd.mmr_conflict_history WHERE relname='%s' AND conflict_type='update_origin_change'" % T2, "1")),
        document_evidence(wait_for("等待场景二 B 记录 insert_exists", B_PORT,
                 "SELECT count(*) FROM fdd.mmr_conflict_history WHERE relname='%s' AND conflict_type='insert_exists'" % T2, "1")),
        document_evidence(conflict_history("展示场景二 A 的完整冲突记录", A_PORT, T2,
                         "返回 update_origin_change、update_pkey_exists、update_if_newer 和 remote", "update_origin_change", "update_pkey_exists", "update_if_newer", "remote")),
        document_evidence(conflict_history("展示场景二 B 的完整冲突记录", B_PORT, T2,
                         "返回 insert_exists、update_if_newer 和 local", "insert_exists", "update_if_newer", "local")),
        document_evidence(final_data("展示场景二 A 最终数据", A_PORT, T2, "返回 id=33,name=jone", "33", "jone")),
        document_evidence(final_data("展示场景二 B 最终数据", B_PORT, T2, "返回 id=33,name=jone", "33", "jone")),
    ] + initial_data("三", T3) + [
        sql("场景三按文档并发：A 更新、B 插入，A 后于 B 提交",
            parallel_transactions(PSQL, A_PORT, "UPDATE public.%s SET id=33 WHERE id=11" % T3, B_PORT, "INSERT INTO public.%s VALUES(33,'jone_new')" % T3, 2, 1),
            "A 返回 UPDATE 1 且后 COMMIT；B 返回 INSERT 0 1 且先 COMMIT",
            "-- A/node134\nBEGIN; UPDATE public.%s SET id=33 WHERE id=11; SELECT pg_sleep(2); COMMIT;\n-- B/node135\nBEGIN; INSERT INTO public.%s VALUES(33,'jone_new'); SELECT pg_sleep(1); COMMIT" % (T3, T3), 35),
        document_evidence(wait_for("等待场景三 A 记录 insert_exists", A_PORT,
                 "SELECT count(*) FROM fdd.mmr_conflict_history WHERE relname='%s' AND conflict_type='insert_exists'" % T3, "1")),
        document_evidence(wait_for("等待场景三 B 记录 update_origin_change", B_PORT,
                 "SELECT count(*) FROM fdd.mmr_conflict_history WHERE relname='%s' AND conflict_type='update_origin_change'" % T3, "1")),
        document_evidence(conflict_history("展示场景三 A 的完整冲突记录", A_PORT, T3,
                         "返回 insert_exists、update_if_newer 和 local", "insert_exists", "update_if_newer", "local")),
        document_evidence(conflict_history("展示场景三 B 的完整冲突记录", B_PORT, T3,
                         "返回 update_origin_change、update_pkey_exists 和 update_if_newer", "update_origin_change", "update_pkey_exists", "update_if_newer")),
        document_evidence(final_data("展示场景三 A 最终数据", A_PORT, T3, "返回 id=33,name=jone", "33", "jone")),
        document_evidence(final_data("展示场景三 B 最终数据", B_PORT, T3, "返回 id=33,name=jone", "33", "jone")),
    ] + initial_data("四", T4) + [
        sql("场景四按文档并发：A 插入、B 更新，A 先于 B 提交",
            parallel_transactions(PSQL, B_PORT, "UPDATE public.%s SET id=33 WHERE id=11" % T4, A_PORT, "INSERT INTO public.%s VALUES(33,'jone_new')" % T4, 2, 1),
            "A 返回 INSERT 0 1 且先 COMMIT；B 返回 UPDATE 1 且后 COMMIT",
            "-- A/node134\nBEGIN; INSERT INTO public.%s VALUES(33,'jone_new'); SELECT pg_sleep(1); COMMIT;\n-- B/node135\nBEGIN; UPDATE public.%s SET id=33 WHERE id=11; SELECT pg_sleep(2); COMMIT" % (T4, T4), 35),
        document_evidence(wait_for("等待场景四 A 记录 update_pkey_exists", A_PORT,
                 "SELECT count(*) FROM fdd.mmr_conflict_history WHERE relname='%s' AND conflict_type='update_pkey_exists'" % T4, "1")),
        document_evidence(wait_for("等待场景四 B 记录 insert_exists", B_PORT,
                 "SELECT count(*) FROM fdd.mmr_conflict_history WHERE relname='%s' AND conflict_type='insert_exists'" % T4, "1")),
        document_evidence(conflict_history("展示场景四 A 的完整冲突记录", A_PORT, T4,
                         "返回 update_pkey_exists、update_if_newer 和 remote", "update_pkey_exists", "update_if_newer", "remote")),
        document_evidence(conflict_history("展示场景四 B 的完整冲突记录", B_PORT, T4,
                         "返回 insert_exists、update_if_newer 和 local", "insert_exists", "update_if_newer", "local")),
        document_evidence(final_data("展示场景四 A 最终数据", A_PORT, T4, "返回 id=33,name=jone", "33", "jone")),
        document_evidence(final_data("展示场景四 B 最终数据", B_PORT, T4, "返回 id=33,name=jone", "33", "jone")),
    ],
    "teardown": "以 immediate 停止临时 A/node134 和 B/node135 PostgreSQL 实例；递归删除临时数据目录，连同四张插入更新冲突测试表、MMR 元数据、订阅、复制槽和四个场景产生的冲突记录一并删除。",
}
