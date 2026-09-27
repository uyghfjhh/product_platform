from suites.mmr.cases.conflict.delete_missing import query, setup, sql
from suites.mmr.cases.node_management.create_group import (PSQL, create_node,
                                                            init_instance, psql, shell)
from suites.mmr.conflict_timing import parallel_transactions


ROOT = "/tmp/fbase_regress_mmr_conflict_identity_full_nopk_{run_id}"
A, B = ROOT + "/a", ROOT + "/b"
A_PORT, B_PORT = "15513", "15514"
DSN = "host=127.0.0.1 port=%s user=postgres dbname=postgres" % A_PORT
TABLE = "identity_full_nopk_{run_id}"


def wait_for(title, port, statement, expected, timeout=35):
    return sql(title,
               "for i in $(seq 1 30); do v=$(%s -X -At -h 127.0.0.1 -p %s -U postgres -d postgres -c %r); test \"$v\" = %r && { echo \"$v\"; exit 0; }; sleep 1; done; exit 1" %
               (PSQL, port, statement, expected), "30 秒内输出 %s" % expected, statement, timeout)


def rows(title, port, expected, *markers):
    return query(title, port, "SELECT id,name,age,city FROM public.%s ORDER BY name" % TABLE,
                 expected, *markers)


CASE = {
    "id": "mmr.conflict.update_update_identity_full_no_pk",
    "name": "REPLICA IDENTITY FULL 无主键表的 UPDATE-UPDATE 冲突",
    "document": "多活功能测试文档.md", "section": "4.3.3.1", "group": "conflict",
    "fixtures": ["cluster", {"type": "isolated_mmr_node_creation", "data_dir": ROOT}],
    "requirements": {"clusters": ["mmr"], "plugins": ["fdd_mmr"], "writable_node": True, "node": "mmr:mmr1"},
    "evidence_nodes": ["mmr:mmr1"],
    "prerequisites": ["隔离 A/node134、B/node135 采用 all 模式组成两节点组；表无主键、设置 REPLICA IDENTITY FULL，初始含两条完全相同的行。", "A/B 分别更新全部重复行，A 后于 B 提交。"],
    "steps": [
        setup(init_instance("初始化 A/node134", A, A_PORT)), setup(create_node(A_PORT, "node134")),
        setup(shell("创建无主键 FULL 复制标识表并写入两条重复初始行", psql(A_PORT, "CREATE TABLE public.%s(id int,name text,age text,city text); ALTER TABLE public.%s REPLICA IDENTITY FULL; INSERT INTO public.%s VALUES(2,'judy','22','A'),(2,'judy','22','A')" % (TABLE,TABLE,TABLE)), "返回 CREATE TABLE、ALTER TABLE 和 INSERT 0 2")),
        setup(shell("创建 g1 集群", psql(A_PORT, "SELECT fdd.create_group('g1')"), "返回 node group create successful")),
        setup(init_instance("初始化 B/node135", B, B_PORT)), setup(create_node(B_PORT, "node135")),
        setup(shell("以 all 模式将 B 加入 g1", psql(B_PORT, "SELECT fdd.join_group('g1','%s',true,'all','table_exist_error')" % DSN), "返回 node join to group complete finished", timeout=90)),
        query("读取 A/node134 实际 debug_logical_replication_streaming", A_PORT, "SHOW debug_logical_replication_streaming", "返回 buffered", "buffered"), query("读取 B/node135 实际 debug_logical_replication_streaming", B_PORT, "SHOW debug_logical_replication_streaming", "返回 buffered", "buffered"),
        query("读取 A/node134 实际 logical_decoding_work_mem", A_PORT, "SHOW logical_decoding_work_mem", "返回 64MB", "64MB"), query("读取 B/node135 实际 logical_decoding_work_mem", B_PORT, "SHOW logical_decoding_work_mem", "返回 64MB", "64MB"),
        query("读取 A/node134 的多活 streaming 模式", A_PORT, "SELECT CASE streaming WHEN 'f' THEN 'off' WHEN 't' THEN 'on' WHEN 'p' THEN 'parallel' END FROM fdd.mmr_node WHERE node_name='node134'", "返回 off", "off"), query("读取 B/node135 的多活 streaming 模式", B_PORT, "SELECT CASE streaming WHEN 'f' THEN 'off' WHEN 't' THEN 'on' WHEN 'p' THEN 'parallel' END FROM fdd.mmr_node WHERE node_name='node135'", "返回 off", "off"),
        rows("展示触发前 A/node134 的两条重复初始数据", A_PORT, "返回两条 id=2,judy,22,A", "2", "judy", "22", "A", "(2 rows)"), rows("展示触发前 B/node135 的两条重复初始数据", B_PORT, "返回两条 id=2,judy,22,A", "2", "judy", "22", "A", "(2 rows)"),
        query("确认触发前 A 没有旧 update_missing 冲突记录", A_PORT, "SELECT count(*) AS conflict_count FROM fdd.mmr_conflict_history WHERE relname='%s' AND conflict_type='update_missing'" % TABLE, "返回 0", "0"), query("确认触发前 B 没有旧 update_missing 冲突记录", B_PORT, "SELECT count(*) AS conflict_count FROM fdd.mmr_conflict_history WHERE relname='%s' AND conflict_type='update_missing'" % TABLE, "返回 0", "0"),
        sql("按文档并发更新两条 FULL 标识行，A 后于 B 提交", parallel_transactions(PSQL, A_PORT, "UPDATE public.%s SET name='A_new'" % TABLE, B_PORT, "UPDATE public.%s SET name='B'" % TABLE, 2, 1), "A/B 均返回 UPDATE 2；B 先 COMMIT、A 后 COMMIT", "-- A/node134\nBEGIN; UPDATE public.%s SET name='A_new'; SELECT pg_sleep(2); COMMIT;\n-- B/node135\nBEGIN; UPDATE public.%s SET name='B'; SELECT pg_sleep(1); COMMIT" % (TABLE,TABLE), 35),
        wait_for("等待 A 记录两条 update_missing", A_PORT, "SELECT count(*) FROM fdd.mmr_conflict_history WHERE relname='%s' AND conflict_type='update_missing'" % TABLE, "2"), wait_for("等待 B 记录两条 update_missing", B_PORT, "SELECT count(*) FROM fdd.mmr_conflict_history WHERE relname='%s' AND conflict_type='update_missing'" % TABLE, "2"),
        query("展示 A 完整 update_missing 冲突记录", A_PORT, "SELECT conflict_type,conflict_resolution,relname,apply_tuple FROM fdd.mmr_conflict_history WHERE relname='%s' ORDER BY local_lsn" % TABLE, "返回两条 update_missing、insert_or_skip、remote", "update_missing", "insert_or_skip", "remote", "(2 rows)"), query("展示 B 完整 update_missing 冲突记录", B_PORT, "SELECT conflict_type,conflict_resolution,relname,apply_tuple FROM fdd.mmr_conflict_history WHERE relname='%s' ORDER BY local_lsn" % TABLE, "返回两条 update_missing、insert_or_skip、remote", "update_missing", "insert_or_skip", "remote", "(2 rows)"),
        rows("展示 A 最终四行数据", A_PORT, "返回两条 A_new 与两条 B", "A_new", "B", "(4 rows)"), rows("展示 B 最终四行数据", B_PORT, "返回两条 A_new 与两条 B", "A_new", "B", "(4 rows)"),
    ],
    "teardown": "以 immediate 停止临时 A/node134 和 B/node135 PostgreSQL 实例；递归删除临时数据目录，连同 FULL 复制标识测试表、MMR 元数据、订阅、复制槽和两端本轮冲突记录一并删除。",
}
