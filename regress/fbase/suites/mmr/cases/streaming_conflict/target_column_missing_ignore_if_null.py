"""Streaming conflict document 2.7.1: target_column_missing ignore_if_null."""

from suites.mmr.cases.streaming_conflict.delete_missing_skip import (
    PORT134, PORT135, sql, wait_for_rows,
)
from suites.mmr.cases.streaming_conflict.update_missing_insert_or_skip import (
    common_streaming_set_steps,
)
from suites.mmr.streaming_conflict_support import non2pc_case_base


TABLE = "immediate_parallel_target_column"

CASE = non2pc_case_base(
    "mmr.streaming_conflict.target_column_missing_ignore_if_null",
    "streaming target_column_missing 的 ignore_if_null 策略", "2.7.1", [
        "发布端 node134：debug_logical_replication_streaming=immediate，logical_decoding_work_mem=64kB。",
        "订阅端 node135：debug_logical_replication_streaming=buffered，logical_decoding_work_mem=64kB。",
        "两端 MMR streaming=parallel；node134 two_phase=false，node135 two_phase=true。",
        "node134 表含 city 默认列，node135 缺少 city；node135 的 target_column_missing=ignore_if_null。",
    ])
CASE["fixtures"] = [
    "cluster",
    {"type": "isolated_mmr_node_creation",
     "data_dir": "/tmp/fbase_regress_mmr_stream_delete_missing_{run_id}"},
]
CASE["steps"] = common_streaming_set_steps() + [
    sql("按文档在 node134 创建含 city 的异构表", PORT134,
        "CREATE TABLE %s(id int PRIMARY KEY,name name,city text DEFAULT 'changsha')" % TABLE,
        "返回 CREATE TABLE", "CREATE TABLE", report_node="node134"),
    sql("按文档在 node135 创建缺少 city 的异构表", PORT135,
        "CREATE TABLE %s(id int PRIMARY KEY,name name)" % TABLE,
        "返回 CREATE TABLE", "CREATE TABLE", report_node="node135"),
    sql("按文档允许异构表节点配置", PORT135,
        "SELECT fdd.alter_mmr_check_node_conf($${\"public.%s\"}$$)" % TABLE,
        "返回 true", "t", report_node="node135"),
    sql("按文档在 node135 将异构表加入 set1", PORT135,
        "SELECT fdd.replication_set_add_table('%s'::regclass,'set1',false,false)" % TABLE,
        "复制集绑定成功", "replication_set_add_table", report_node="node135"),
    sql("按文档在所有节点订阅 set1", PORT134,
        "SELECT fdd.run_on_all_nodes('select fdd.alter_node_replication_sets(''{set1}'');')",
        "node134/node135 均成功", "node134", "node135", report_node="node134"),
    sql("按文档在 node135 异步执行复制集变更", PORT135,
        "SELECT fdd.replication_set_async_execute()", "异步生效",
        "replication_set_async_execute", report_node="node135"),
    sql("将 node135 target_column_missing 设为 ignore_if_null", PORT135,
        "SELECT fdd.alter_local_node_set_conflict_resolver('target_column_missing','ignore_if_null')",
        "返回 node135,target_column_missing,ignore_if_null",
        "node135,target_column_missing,ignore_if_null", report_node="node135"),
    sql("确认触发前 ignore_if_null 冲突记录为 0", PORT135,
        "SELECT count(*) FROM fdd.mmr_conflict_history WHERE relname='%s' AND conflict_type='target_column_missing' AND conflict_resolution='ignore_if_null'" % TABLE,
        "返回 0", "0", report_node="node135"),
    sql("按文档插入带非 NULL city 的 512 行事务", PORT134,
        "BEGIN; INSERT INTO %s(id,name,city) SELECT gs,repeat('a',128), 'changsha' FROM generate_series(100001,100512) gs; COMMIT" % TABLE,
        "返回 BEGIN、INSERT 0 512、COMMIT", "BEGIN", "INSERT 0 512", "COMMIT",
        report_node="node134"),
    wait_for_rows("确认 node135 未插入数据且记录首条 ignore_if_null 冲突", PORT135,
                  "SELECT (SELECT count(*) FROM %s WHERE id BETWEEN 100001 AND 100512)::text || '|' || (SELECT count(*) FROM fdd.mmr_conflict_history WHERE relname='%s' AND conflict_type='target_column_missing' AND conflict_resolution='ignore_if_null')::text" %
                  (TABLE, TABLE), "0|1", "node135"),
]
