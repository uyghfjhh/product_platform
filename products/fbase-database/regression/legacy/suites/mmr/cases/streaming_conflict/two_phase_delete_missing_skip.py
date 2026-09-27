"""Streaming conflict document 3.1.1: 2PC delete_missing/skip."""

from suites.mmr.cases.streaming_conflict.delete_missing_skip import PORT134, PORT135, sql, wait_for_rows
from suites.mmr.streaming_conflict_support import non2pc_case_base
from suites.mmr.two_phase_conflict_support import two_phase_streaming_set_steps


TABLE = "immediate_parallel_2pc"

CASE = non2pc_case_base(
    "mmr.streaming_conflict.two_phase_delete_missing_skip",
    "2PC streaming delete_missing 的 skip 策略", "3.1.1", [
        "node134=parallel,two_phase=false；node135=parallel,two_phase=true。",
        "node134=immediate、node135=buffered，logical_decoding_work_mem=64kB。",
        "node135 的 delete_missing=skip；分别验证 rollback prepared 与 commit prepared。",
    ])
CASE["steps"] = two_phase_streaming_set_steps() + [
    sql("按文档在 node134 创建 2PC 测试表", PORT134,
        "CREATE TABLE %s(id int PRIMARY KEY,name text)" % TABLE,
        "返回 CREATE TABLE", "CREATE TABLE", report_node="node134"),
    sql("按文档在 node134 插入初始 100 行", PORT134,
        "INSERT INTO %s VALUES (generate_series(1,100),'a')" % TABLE,
        "返回 INSERT 0 100", "INSERT 0 100", report_node="node134"),
    sql("按文档在 node135 创建空 2PC 测试表", PORT135,
        "CREATE TABLE %s(id int PRIMARY KEY,name text)" % TABLE,
        "返回 CREATE TABLE", "CREATE TABLE", report_node="node135"),
    sql("按文档在 node135 将 2PC 表加入 set1", PORT135,
        "SELECT fdd.replication_set_add_table('%s'::regclass,'set1',false,false)" % TABLE,
        "复制集绑定成功", "replication_set_add_table", report_node="node135"),
    sql("按文档在所有节点订阅 set1", PORT134,
        "SELECT fdd.run_on_all_nodes('select fdd.alter_node_replication_sets(''{set1}'');')",
        "node134/node135 均成功", "node134", "node135", report_node="node134"),
    sql("按文档在 node135 异步执行复制集变更", PORT135,
        "SELECT fdd.replication_set_async_execute()", "异步生效", "replication_set_async_execute", report_node="node135"),
    sql("确认 node135 初始表为空", PORT135, "SELECT count(*) FROM %s" % TABLE,
        "返回 0", "0", report_node="node135"),
    sql("将 node135 delete_missing 设为 skip", PORT135,
        "SELECT fdd.alter_local_node_set_conflict_resolver('delete_missing','skip')",
        "返回 node135,delete_missing,skip", "node135,delete_missing,skip", report_node="node135"),
    sql("确认触发前 skip 历史为 0", PORT135,
        "SELECT count(*) FROM fdd.mmr_conflict_history WHERE relname='%s' AND conflict_type='delete_missing' AND conflict_resolution='skip'" % TABLE,
        "返回 0", "0", report_node="node135"),
    sql("按文档插入 512 行 streaming 前置数据", PORT134,
        "INSERT INTO %s(id,name) SELECT gs,repeat('a',128) FROM generate_series(100001,100512) gs" % TABLE,
        "返回 INSERT 0 512", "INSERT 0 512", report_node="node134"),
    wait_for_rows("等待 node135 收到 512 行", PORT135,
        "SELECT count(*) FROM %s WHERE id BETWEEN 100001 AND 100512" % TABLE, "512", "node135"),
    sql("按文档准备第一次删除事务", PORT134,
        "BEGIN; DELETE FROM %s WHERE id=100 OR id BETWEEN 100001 AND 100512; PREPARE TRANSACTION 'stream_100'" % TABLE,
        "返回 BEGIN、DELETE 513、PREPARE TRANSACTION", "BEGIN", "DELETE 513", "PREPARE TRANSACTION", report_node="node134"),
    sql("按文档回滚第一次预备事务", PORT134, "ROLLBACK PREPARED 'stream_100'",
        "返回 ROLLBACK PREPARED", "ROLLBACK PREPARED", report_node="node134"),
    wait_for_rows("确认 rollback 后 node134 保留 513 行、node135 保留 512 行且无历史", PORT135,
        "SELECT (SELECT count(*) FROM %s WHERE id=100 OR id BETWEEN 100001 AND 100512)::text || '|' || (SELECT count(*) FROM fdd.mmr_conflict_history WHERE relname='%s' AND conflict_type='delete_missing' AND conflict_resolution='skip')::text" % (TABLE,TABLE),
        "512|0", "node135"),
    sql("按文档准备第二次删除事务", PORT134,
        "BEGIN; DELETE FROM %s WHERE id=100 OR id BETWEEN 100001 AND 100512; PREPARE TRANSACTION 'stream_100'" % TABLE,
        "返回 BEGIN、DELETE 513、PREPARE TRANSACTION", "BEGIN", "DELETE 513", "PREPARE TRANSACTION", report_node="node134"),
    sql("按文档提交第二次预备事务", PORT134, "COMMIT PREPARED 'stream_100'",
        "返回 COMMIT PREPARED", "COMMIT PREPARED", report_node="node134"),
    wait_for_rows("确认 commit 后 node135 删除 512 行并记录一条 skip 历史", PORT135,
        "SELECT (SELECT count(*) FROM %s WHERE id=100 OR id BETWEEN 100001 AND 100512)::text || '|' || (SELECT count(*) FROM fdd.mmr_conflict_history WHERE relname='%s' AND conflict_type='delete_missing' AND conflict_resolution='skip')::text" % (TABLE,TABLE),
        "0|1", "node135"),
]
