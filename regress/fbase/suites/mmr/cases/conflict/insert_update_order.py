from suites.mmr.cases.conflict.delete_missing import query, sql
from suites.mmr.cases.conflict.update_insert_order import (A_PORT, B_PORT, CASE as BASE,
                                                            PSQL, TABLE)
from suites.mmr.conflict_timing import parallel_transactions

TABLE_B = "insert_update_b_{run_id}"


def replace_step(title, script, expected, statement):
    return sql(title, script, expected, statement, 35)


CASE = dict(BASE)
CASE.update({
    "id": "mmr.conflict.insert_update_order",
    "name": "插入先提交、更新后提交的数据一致性",
    "section": "4.3.1.2",
    "prerequisites": [
        "初始 id=11 由 A/node134 写入并同步到 B/node135；B 先完成 UPDATE，A 的 INSERT 先提交，B 后提交。",
    ],
})

# Reuse the isolated topology and initial-data evidence, then replace every
# transaction-dependent assertion with the documented inverse commit order.
CASE["steps"] = [dict(step) for step in BASE["steps"][:9]] + [
    replace_step(
        "按文档让 A 插入先提交、B 更新后提交",
        parallel_transactions(PSQL, B_PORT, "UPDATE public.%s SET id=33 WHERE id=11" % TABLE,
                              A_PORT, "INSERT INTO public.%s VALUES(33,'jone_new')" % TABLE, 2, 1),
        "A 返回 INSERT 0 1 且先 COMMIT；B 返回 UPDATE 1 且后 COMMIT",
        "-- A/node134\nBEGIN; INSERT INTO public.%s VALUES(33,'jone_new'); SELECT pg_sleep(1); COMMIT;\n-- B/node135\nBEGIN; UPDATE public.%s SET id=33 WHERE id=11; SELECT pg_sleep(2); COMMIT" % (TABLE, TABLE)),
    replace_step(
        "等待 B 记录 insert_exists",
        "for i in $(seq 1 30); do v=$(%s -X -At -h 127.0.0.1 -p %s -U postgres -d postgres -c %r); test \"$v\" = 1 && { echo \"$v\"; exit 0; }; sleep 1; done; exit 1" %
        (PSQL, B_PORT, "SELECT count(*) FROM fdd.mmr_conflict_history WHERE conflict_type='insert_exists'"),
        "30 秒内 B 存在一条 insert_exists", "SELECT count(*) FROM fdd.mmr_conflict_history WHERE conflict_type='insert_exists'"),
    query("展示 A 最终数据", A_PORT, "SELECT id,name FROM public.%s ORDER BY id" % TABLE, "返回 id=33,jone", "33", "jone"),
    query("展示 B 最终数据", B_PORT, "SELECT id,name FROM public.%s ORDER BY id" % TABLE, "返回 id=33,jone", "33", "jone"),
    query("展示 B 的 insert_exists 冲突记录", B_PORT, "SELECT conflict_type,conflict_resolution,relname,apply_tuple FROM fdd.mmr_conflict_history WHERE conflict_type='insert_exists' ORDER BY local_lsn DESC LIMIT 1", "返回 insert_exists、update_if_newer", "insert_exists", "update_if_newer"),
    sql("为初始数据由 B 写入的场景创建第二张表", PSQL + " -X -v ON_ERROR_STOP=1 -h 127.0.0.1 -p %s -U postgres -d postgres -c %r; " % (A_PORT, "SELECT fdd.run_on_all_nodes('CREATE TABLE public.%s(id int PRIMARY KEY,name varchar)')" % TABLE_B) + PSQL + " -X -v ON_ERROR_STOP=1 -h 127.0.0.1 -p %s -U postgres -d postgres -c %r" % (A_PORT, "SELECT fdd.replication_set_async_execute(true)"), "两个节点均创建第二张表并完成异步处理", "SELECT fdd.run_on_all_nodes('CREATE TABLE public.%s(id int PRIMARY KEY,name varchar)'); SELECT fdd.replication_set_async_execute(true)" % TABLE_B),
    sql("按文档由 B 写入第二场景的初始 id=11", PSQL + " -X -v ON_ERROR_STOP=1 -h 127.0.0.1 -p %s -U postgres -d postgres -c %r" % (B_PORT, "INSERT INTO public.%s VALUES(11,'jone')" % TABLE_B), "返回 INSERT 0 1", "INSERT INTO public.%s VALUES(11,'jone')" % TABLE_B),
    replace_step("等待 A 接收 B 的初始 id=11", "for i in $(seq 1 30); do v=$(%s -X -At -h 127.0.0.1 -p %s -U postgres -d postgres -c %r); test \"$v\" = jone && { echo \"$v\"; exit 0; }; sleep 1; done; exit 1" % (PSQL,A_PORT,"SELECT name FROM public.%s WHERE id=11" % TABLE_B), "30 秒内输出 jone", "SELECT name FROM public.%s WHERE id=11" % TABLE_B),
    query("展示第二场景 A/B 初始数据", A_PORT, "SELECT id,name FROM public.%s" % TABLE_B, "返回 id=11,jone", "11", "jone"),
    query("确认第二场景没有旧的 B 表 insert_exists 记录", B_PORT, "SELECT count(*) AS count FROM fdd.mmr_conflict_history WHERE conflict_type='insert_exists' AND relname='%s'" % TABLE_B, "返回 0", "0"),
    replace_step("第二场景让 A 插入先提交、B 更新后提交", parallel_transactions(PSQL, B_PORT, "UPDATE public.%s SET id=33 WHERE id=11" % TABLE_B, A_PORT, "INSERT INTO public.%s VALUES(33,'jone_new')" % TABLE_B, 2, 1), "A INSERT 先 COMMIT，B UPDATE 后 COMMIT", "-- A/node134\nBEGIN; INSERT INTO public.%s VALUES(33,'jone_new'); SELECT pg_sleep(1); COMMIT;\n-- B/node135\nBEGIN; UPDATE public.%s SET id=33 WHERE id=11; SELECT pg_sleep(2); COMMIT" % (TABLE_B,TABLE_B)),
    replace_step("等待 B 记录第二场景的 insert_exists", "for i in $(seq 1 30); do v=$(%s -X -At -h 127.0.0.1 -p %s -U postgres -d postgres -c %r); test \"$v\" = 1 && { echo \"$v\"; exit 0; }; sleep 1; done; exit 1" % (PSQL,B_PORT,"SELECT count(*) FROM fdd.mmr_conflict_history WHERE conflict_type='insert_exists' AND relname='%s'" % TABLE_B), "30 秒内 B 存在一条第二场景 insert_exists", "SELECT count(*) FROM fdd.mmr_conflict_history WHERE conflict_type='insert_exists'"),
    query("展示第二场景 A 最终数据", A_PORT, "SELECT id,name FROM public.%s" % TABLE_B, "返回 id=33,jone", "33", "jone"),
    query("展示第二场景 B 最终数据", B_PORT, "SELECT id,name FROM public.%s" % TABLE_B, "返回 id=33,jone", "33", "jone"),
]
