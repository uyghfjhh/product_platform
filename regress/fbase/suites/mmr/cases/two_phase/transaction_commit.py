from framework.assertions import output_contains_text
from suites.mmr.cases.conflict.delete_missing import query, setup
from suites.mmr.cases.node_management.create_group import (PSQL, create_node,
                                                            init_instance, psql, shell)


ROOT = "/tmp/fbase_regress_mmr_2pc_transaction_{run_id}"
SOURCE, TARGET = ROOT + "/node134", ROOT + "/node135"
SOURCE_PORT, TARGET_PORT = "15675", "15676"
TABLE = "fbase_r_mmr_2pc_{run_id}"
GID = "fbase_r_mmr_2pc_{run_id}"
DSN = "host=127.0.0.1 port=%s user=postgres dbname=postgres" % SOURCE_PORT


def wait_for_scalar(title, port, statement, expected, timeout=30):
    script = (
        "v=''; for i in $(seq 1 %s); do "
        "v=$(%s -X -At -F '|' -h 127.0.0.1 -p %s -U postgres -d postgres -c %r); "
        "test \"$v\" = %r && { echo \"$v\"; exit 0; }; sleep 1; "
        "done; echo \"$v\"; exit 1" %
        (timeout, PSQL, port, statement, expected))
    step = shell(title, script, "在 %s 秒内输出 %s" % (timeout, expected),
                 output_contains_text(expected), timeout=timeout + 5)
    step["display_sql"] = statement
    return step


CASE = {
    "id": "mmr.two_phase.transaction_commit", "name": "多活两阶段提交",
    "document": "多活功能测试文档.md", "section": "9.3", "group": "two_phase",
    "fixtures": ["cluster", {"type": "isolated_mmr_node_creation", "data_dir": ROOT}],
    "requirements": {"clusters": ["mmr"], "plugins": ["fdd_mmr"],
                     "writable_node": True, "node": "mmr:mmr1"},
    "evidence_nodes": ["mmr:mmr1"],
    "prerequisites": [
        "严格按 9.3：两端先创建同构表；node134 建组后插入基线行；node135 以 all 模式加入。",
        "两个成员创建时均开启 two_phase；在预备前确认 node135 的订阅 two-phase 已进入 enabled。",
    ],
    "steps": [
        setup(init_instance("初始化 node134", SOURCE, SOURCE_PORT)),
        setup(shell("在 node134 创建文档两阶段提交测试表", psql(SOURCE_PORT,
                    "CREATE TABLE public.%s(id int PRIMARY KEY)" % TABLE),
                    "返回 CREATE TABLE")),
        setup(create_node(SOURCE_PORT, "node134", two_phase=True)),
        setup(shell("在 node134 创建 g1 集群", psql(SOURCE_PORT,
                    "SELECT fdd.create_group('g1')"),
                    "返回 node group create successful")),
        setup(shell("在 node134 插入文档基线行", psql(SOURCE_PORT,
                    "INSERT INTO public.%s VALUES(1)" % TABLE), "返回 INSERT 0 1")),
        setup(init_instance("初始化 node135", TARGET, TARGET_PORT)),
        setup(shell("在 node135 创建同构测试表", psql(TARGET_PORT,
                    "CREATE TABLE public.%s(id int PRIMARY KEY)" % TABLE),
                    "返回 CREATE TABLE")),
        setup(create_node(TARGET_PORT, "node135", two_phase=True)),
        setup(shell("按文档以 all 模式将 node135 加入 g1", psql(TARGET_PORT,
                    "SELECT fdd.join_group('g1','%s',true,'all','table_exist_error')" % DSN),
                    "返回 node join to group complete finished", timeout=90)),
        query("确认两成员均启用 two_phase", SOURCE_PORT,
              "SELECT node_name,two_phase FROM fdd.mmr_node ORDER BY node_name",
              "返回 node134|t 和 node135|t", "node134", "t", "node135"),
        wait_for_scalar("确认 node135 订阅 two-phase 已启用", TARGET_PORT,
                        "SELECT count(*)::text || '|' || bool_and(subtwophasestate='e')::text FROM pg_subscription",
                        "1|true"),
        query("展示预备前 node134 的初始数据", SOURCE_PORT,
              "SELECT id FROM public.%s ORDER BY id" % TABLE, "仅返回基线 id=1", "1"),
        query("展示预备前 node135 的初始数据", TARGET_PORT,
              "SELECT id FROM public.%s ORDER BY id" % TABLE, "仅返回 all 加入复制的基线 id=1", "1"),
        shell("按文档预备包含第二行的事务", psql(SOURCE_PORT,
              "BEGIN; INSERT INTO public.%s VALUES(2); PREPARE TRANSACTION '%s'" % (TABLE, GID)),
              "返回 BEGIN、INSERT 0 1、PREPARE TRANSACTION"),
        query("确认源端保留原始 GID 且第二行尚未提交", SOURCE_PORT,
              "SELECT (SELECT count(*)::text FROM pg_prepared_xacts WHERE gid='%s') || '|' || "
              "(SELECT count(*)::text FROM public.%s)" % (GID, TABLE),
              "返回 1|1", "1|1"),
        wait_for_scalar("确认 node135 收到转换后的预备事务", TARGET_PORT,
                        "SELECT count(*)::text || '|' || bool_and(gid LIKE 'pg_gid_%%')::text || '|' || "
                        "bool_and(gid <> '%s')::text FROM pg_prepared_xacts" % GID,
                        "1|true|true"),
        query("展示 node135 转换后的预备事务 GID", TARGET_PORT,
              "SELECT gid FROM pg_prepared_xacts", "返回 pg_gid_ 开头且不等于源端 GID", "pg_gid_"),
        shell("按文档提交源端预备事务", psql(SOURCE_PORT,
              "COMMIT PREPARED '%s'" % GID), "返回 COMMIT PREPARED"),
        wait_for_scalar("确认 node135 已提交第二行且预备事务消失", TARGET_PORT,
                        "SELECT (SELECT count(*)::text FROM pg_prepared_xacts) || '|' || "
                        "(SELECT count(*)::text FROM public.%s)" % TABLE, "0|2"),
        query("展示两阶段提交后 node134 数据", SOURCE_PORT,
              "SELECT id FROM public.%s ORDER BY id" % TABLE, "返回 id=1、2", "1", "2"),
        query("展示两阶段提交后 node135 数据", TARGET_PORT,
              "SELECT id FROM public.%s ORDER BY id" % TABLE, "返回 id=1、2", "1", "2"),
    ],
    "teardown": "fixture 以 immediate 停止临时 node134/node135 PostgreSQL 实例；递归删除临时数据目录，连同测试表、MMR 元数据、订阅、复制槽和预备事务一并删除。",
}
