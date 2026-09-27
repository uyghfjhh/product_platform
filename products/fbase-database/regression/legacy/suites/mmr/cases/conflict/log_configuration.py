from suites.mmr.cases.conflict.delete_missing import query, setup, sql
from suites.mmr.cases.node_management.create_group import (PSQL, create_node,
                                                            init_instance, psql, shell)
from suites.mmr.conflict_timing import parallel_transactions


ROOT = "/tmp/fbase_regress_mmr_conflict_log_config_{run_id}"
A, B = ROOT + "/a", ROOT + "/b"
A_PORT, B_PORT = "15519", "15520"
DSN = "host=127.0.0.1 port=%s user=postgres dbname=postgres" % A_PORT
INSERT, INSERT_FILTER, INCLUDED, PKEY, REMOTE = ("log_config_%s_{run_id}" % key for key in ("insert", "insert_filter", "included", "pkey", "remote"))
FILTER_TYPES = "'{update_missing,update_recently_deleted,update_pkey_exists,update_origin_change}'"
FILTER_RESULTS = "'{apply_remote,skip}'"


def wait_for(title, port, statement, expected, timeout=35):
    return sql(title, "for i in $(seq 1 30); do v=$(%s -X -At -h 127.0.0.1 -p %s -U postgres -d postgres -c %r); test \"$v\" = %r && { echo \"$v\"; exit 0; }; sleep 1; done; exit 1" % (PSQL, port, statement, expected), "30 秒内输出 %s" % expected, statement, timeout)


def history(title, port, table, expected, *markers):
    return query(title, port, "SELECT conflict_type,conflict_resolution,relname,apply_tuple FROM fdd.mmr_conflict_history WHERE relname='%s' ORDER BY local_lsn" % table, expected, *markers)


def race(title, a, b, expected, display):
    return sql(title, parallel_transactions(PSQL, A_PORT, a, B_PORT, b, 2, 1), expected, display, 35)


def race_a_first(title, a, b, expected, display):
    return sql(title, parallel_transactions(PSQL, A_PORT, a, B_PORT, b, 1, 2), expected, display, 35)


DDL = "; ".join(["CREATE TABLE public.%s(id int PRIMARY KEY,name text)" % t for t in (INSERT,INSERT_FILTER,INCLUDED,PKEY,REMOTE)] + ["INSERT INTO public.%s VALUES(1,'base')" % t for t in (INCLUDED,PKEY,REMOTE)])
CONFIG_QUERY = "SELECT log_to_file,log_to_table,conflict_type::text,conflict_res::text FROM fdd.mmr_conflict_log_config"


CASE = {
    "id": "mmr.conflict.log_configuration", "name": "冲突记录配置的本地、远端和全节点控制", "document": "多活功能测试文档.md", "section": "4.4", "group": "conflict",
    "fixtures": ["cluster", {"type": "isolated_mmr_node_creation", "data_dir": ROOT}],
    "requirements": {"clusters": ["mmr"], "plugins": ["fdd_mmr"], "writable_node": True, "node": "mmr:mmr1"}, "evidence_nodes": ["mmr:mmr1"],
    "prerequisites": ["隔离 A/node134、B/node135 采用 all 模式组成两节点组；四张表分别用于默认记录、类型过滤、处理结果过滤和远端节点配置。", "日志由临时实例 pg_ctl 的 start.log 接收；验证时通过 pg_read_file 以 psql 形式读取该本轮日志。"],
    "steps": [
        setup(init_instance("初始化 A/node134", A, A_PORT)), setup(create_node(A_PORT, "node134")), setup(shell("创建五张冲突日志配置测试表及基础数据", psql(A_PORT, DDL), "返回 CREATE TABLE 和三次 INSERT 0 1")), setup(shell("创建 g1 集群", psql(A_PORT, "SELECT fdd.create_group('g1')"), "返回 node group create successful")), setup(init_instance("初始化 B/node135", B, B_PORT)), setup(create_node(B_PORT, "node135")), setup(shell("以 all 模式将 B 加入 g1", psql(B_PORT, "SELECT fdd.join_group('g1','%s',true,'all','table_exist_error')" % DSN), "返回 node join to group complete finished", timeout=90)),
        query("读取 A/node134 实际 debug_logical_replication_streaming", A_PORT, "SHOW debug_logical_replication_streaming", "返回 buffered", "buffered"), query("读取 B/node135 实际 debug_logical_replication_streaming", B_PORT, "SHOW debug_logical_replication_streaming", "返回 buffered", "buffered"), query("读取 A/node134 实际 logical_decoding_work_mem", A_PORT, "SHOW logical_decoding_work_mem", "返回 64MB", "64MB"), query("读取 B/node135 实际 logical_decoding_work_mem", B_PORT, "SHOW logical_decoding_work_mem", "返回 64MB", "64MB"), query("读取 A/node134 的多活 streaming 模式", A_PORT, "SELECT CASE streaming WHEN 'f' THEN 'off' WHEN 't' THEN 'on' WHEN 'p' THEN 'parallel' END FROM fdd.mmr_node WHERE node_name='node134'", "返回 off", "off"), query("读取 B/node135 的多活 streaming 模式", B_PORT, "SELECT CASE streaming WHEN 'f' THEN 'off' WHEN 't' THEN 'on' WHEN 'p' THEN 'parallel' END FROM fdd.mmr_node WHERE node_name='node135'", "返回 off", "off"),
        query("展示 A 默认冲突记录配置", A_PORT, CONFIG_QUERY, "返回 log_to_file=false、log_to_table=true、无过滤", "f", "t"), query("展示 B 默认冲突记录配置", B_PORT, CONFIG_QUERY, "返回 log_to_file=false、log_to_table=true、无过滤", "f", "t"),
        query("确认默认插入冲突触发前 A 无旧记录", A_PORT, "SELECT count(*) AS conflict_count FROM fdd.mmr_conflict_history WHERE relname='%s'" % INSERT, "返回 0", "0"), query("确认默认插入冲突触发前 B 无旧记录", B_PORT, "SELECT count(*) AS conflict_count FROM fdd.mmr_conflict_history WHERE relname='%s'" % INSERT, "返回 0", "0"),
        race("默认配置下两端并发插入同一主键", "INSERT INTO public.%s VALUES(1,'aa')" % INSERT, "INSERT INTO public.%s VALUES(1,'bb')" % INSERT, "两端均 INSERT 0 1，B 先 COMMIT、A 后 COMMIT", "-- A/node134\nBEGIN; INSERT INTO public.%s VALUES(1,'aa'); SELECT pg_sleep(2); COMMIT;\n-- B/node135\nBEGIN; INSERT INTO public.%s VALUES(1,'bb'); SELECT pg_sleep(1); COMMIT" % (INSERT,INSERT)),
        wait_for("等待默认配置下 A 记录 insert_exists", A_PORT, "SELECT count(*) FROM fdd.mmr_conflict_history WHERE relname='%s' AND conflict_type='insert_exists'" % INSERT, "1"), wait_for("等待默认配置下 B 记录 insert_exists", B_PORT, "SELECT count(*) FROM fdd.mmr_conflict_history WHERE relname='%s' AND conflict_type='insert_exists'" % INSERT, "1"), history("展示默认配置下 A insert_exists 记录", A_PORT, INSERT, "返回 insert_exists、update_if_newer", "insert_exists", "update_if_newer"), history("展示默认配置下 B insert_exists 记录", B_PORT, INSERT, "返回 insert_exists、update_if_newer", "insert_exists", "update_if_newer"),
        sql("按文档仅修改 A/node134 的冲突记录配置", psql(A_PORT, "SELECT fdd.alter_node_set_log_config('node134',true,true,%s,%s)" % (FILTER_TYPES,FILTER_RESULTS)), "返回一行空值，配置更新成功", "SELECT fdd.alter_node_set_log_config('node134',true,true,%s,%s)" % (FILTER_TYPES,FILTER_RESULTS)), query("展示 A 过滤后的冲突记录配置", A_PORT, CONFIG_QUERY, "返回 log_to_file=true、log_to_table=true、四种类型和 apply_remote/skip", "t", "update_missing", "update_recently_deleted", "update_pkey_exists", "update_origin_change", "apply_remote", "skip"),
        race("过滤配置下在独立表并发插入，验证 insert_exists 不记录到 A 历史表", "INSERT INTO public.%s VALUES(1,'aa')" % INSERT_FILTER, "INSERT INTO public.%s VALUES(1,'bb')" % INSERT_FILTER, "两端均 INSERT 0 1，B 先 COMMIT、A 后 COMMIT", "-- A/node134\nBEGIN; INSERT INTO public.%s VALUES(1,'aa'); SELECT pg_sleep(2); COMMIT;\n-- B/node135\nBEGIN; INSERT INTO public.%s VALUES(1,'bb'); SELECT pg_sleep(1); COMMIT" % (INSERT_FILTER,INSERT_FILTER)),
        query("确认 A 按类型过滤后未记录 insert_exists", A_PORT, "SELECT count(*) AS insert_exists_count FROM fdd.mmr_conflict_history WHERE relname='%s' AND conflict_type='insert_exists'" % INSERT_FILTER, "返回 0", "0"), query("确认 B 默认配置仍记录 insert_exists", B_PORT, "SELECT count(*) AS insert_exists_count FROM fdd.mmr_conflict_history WHERE relname='%s' AND conflict_type='insert_exists'" % INSERT_FILTER, "返回 1", "1"),
        race_a_first("过滤配置下触发 update_missing 和 delete_recently_updated", "DELETE FROM public.%s WHERE id=1" % INCLUDED, "UPDATE public.%s SET name='included' WHERE id=1" % INCLUDED, "A DELETE 1 先提交；B UPDATE 1 后提交", "-- A/node134\nBEGIN; DELETE FROM public.%s WHERE id=1; SELECT pg_sleep(1); COMMIT;\n-- B/node135\nBEGIN; UPDATE public.%s SET name='included' WHERE id=1; SELECT pg_sleep(2); COMMIT" % (INCLUDED,INCLUDED)),
        wait_for("等待 A 记录允许的 update_missing", A_PORT, "SELECT count(*) FROM fdd.mmr_conflict_history WHERE relname='%s' AND conflict_type='update_missing'" % INCLUDED, "1"), history("展示 A 允许的 update_missing 冲突记录", A_PORT, INCLUDED, "返回 update_missing、insert_or_skip、remote", "update_missing", "insert_or_skip", "remote"), query("确认 A 服务端日志记录允许的 update_missing", A_PORT, "SELECT pg_read_file('%s/a/start.log') LIKE '%%mmr_conflict occurs, type:update_missing%%' AS has_update_missing_log" % ROOT, "返回 true", "t"),
        race("过滤配置下触发 update_pkey_exists 的 local 结果", "UPDATE public.%s SET id=1001 WHERE id=1" % PKEY, "UPDATE public.%s SET id=1001 WHERE id=1" % PKEY, "两端均 UPDATE 1，B 先 COMMIT、A 后 COMMIT", "-- A/node134\nBEGIN; UPDATE public.%s SET id=1001 WHERE id=1; SELECT pg_sleep(2); COMMIT;\n-- B/node135\nBEGIN; UPDATE public.%s SET id=1001 WHERE id=1; SELECT pg_sleep(1); COMMIT" % (PKEY,PKEY)),
        wait_for("等待 B 默认配置记录 update_pkey_exists", B_PORT, "SELECT count(*) FROM fdd.mmr_conflict_history WHERE relname='%s' AND conflict_type='update_pkey_exists'" % PKEY, "1"), query("确认 A 按处理结果过滤后未记录 apply_local 的 update_pkey_exists", A_PORT, "SELECT count(*) AS pkey_count FROM fdd.mmr_conflict_history WHERE relname='%s'" % PKEY, "返回 0", "0"),
        sql("从 A/node134 按文档修改 B/node135 的远端日志配置", psql(A_PORT, "SELECT fdd.alter_node_set_log_config('node135',true,true,%s,%s)" % (FILTER_TYPES,FILTER_RESULTS)), "返回一行空值，远端配置更新成功", "SELECT fdd.alter_node_set_log_config('node135',true,true,%s,%s)" % (FILTER_TYPES,FILTER_RESULTS)), query("展示 B 被远端更新后的冲突记录配置", B_PORT, CONFIG_QUERY, "返回 log_to_file=true、log_to_table=true、四种类型和 apply_remote/skip", "t", "update_missing", "update_recently_deleted", "update_pkey_exists", "update_origin_change", "apply_remote", "skip"),
        race_a_first("远端配置后触发 B 允许的 update_recently_deleted", "UPDATE public.%s SET name='remote' WHERE id=1" % REMOTE, "DELETE FROM public.%s WHERE id=1" % REMOTE, "A UPDATE 1 先提交；B DELETE 1 后提交", "-- A/node134\nBEGIN; UPDATE public.%s SET name='remote' WHERE id=1; SELECT pg_sleep(1); COMMIT;\n-- B/node135\nBEGIN; DELETE FROM public.%s WHERE id=1; SELECT pg_sleep(2); COMMIT" % (REMOTE,REMOTE)),
        wait_for("等待 B 记录允许的 update_recently_deleted", B_PORT, "SELECT count(*) FROM fdd.mmr_conflict_history WHERE relname='%s' AND conflict_type='update_recently_deleted'" % REMOTE, "1"), history("展示 B 远端配置生效后的冲突记录", B_PORT, REMOTE, "返回 update_recently_deleted、skip、none", "update_recently_deleted", "skip", "none"),
        sql("按文档以 NULL 节点名恢复所有节点默认冲突记录配置", psql(A_PORT, "SELECT fdd.alter_node_set_log_config(NULL,false,true,NULL,NULL)"), "返回一行空值，所有节点配置恢复", "SELECT fdd.alter_node_set_log_config(NULL,false,true,NULL,NULL)"), query("确认 A 已恢复默认配置", A_PORT, CONFIG_QUERY, "返回 log_to_file=false、log_to_table=true、无过滤", "f", "t"), query("确认 B 已恢复默认配置", B_PORT, CONFIG_QUERY, "返回 log_to_file=false、log_to_table=true、无过滤", "f", "t"),
    ],
    "teardown": "以 immediate 停止临时 A/node134 和 B/node135 PostgreSQL 实例；递归删除临时数据目录，连同五张日志配置测试表、MMR 元数据、订阅、复制槽、冲突记录和临时 start.log 一并删除。",
}
