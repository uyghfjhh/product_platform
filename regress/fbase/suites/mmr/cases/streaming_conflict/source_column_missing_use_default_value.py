"""Streaming conflict document 2.8.1: source_column_missing use_default_value."""

from suites.mmr.cases.streaming_conflict.delete_missing_skip import (
    PORT134, PORT135, sql, wait_for_rows,
)
from suites.mmr.cases.streaming_conflict.update_missing_insert_or_skip import (
    common_streaming_set_steps,
)
from suites.mmr.streaming_conflict_support import non2pc_case_base


TABLE = "immediate_parallel_source_column"

CASE = non2pc_case_base(
    "mmr.streaming_conflict.source_column_missing_use_default_value",
    "streaming source_column_missing 的 use_default_value 策略", "2.8.1", [
        "发布端 node134：debug_logical_replication_streaming=immediate，logical_decoding_work_mem=64kB。",
        "订阅端 node135：debug_logical_replication_streaming=buffered，logical_decoding_work_mem=64kB。",
        "两端 MMR streaming=parallel；node134 two_phase=false，node135 two_phase=true。",
        "node134 缺少 city，node135 的 city 有默认值；node135 的 source_column_missing=use_default_value。",
    ])
CASE["fixtures"] = [
    "cluster",
    {"type": "isolated_mmr_node_creation",
     "data_dir": "/tmp/fbase_regress_mmr_stream_delete_missing_{run_id}"},
]
CASE["steps"] = common_streaming_set_steps() + [
    sql("按文档在 node134 创建缺少 city 的异构表", PORT134,
        "CREATE TABLE %s(id int PRIMARY KEY,name name)" % TABLE,
        "返回 CREATE TABLE", "CREATE TABLE", report_node="node134"),
    sql("按文档在 node135 创建含 city 默认列的异构表", PORT135,
        "CREATE TABLE %s(id int PRIMARY KEY,name name,city text DEFAULT 'changsha')" % TABLE,
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
        "SELECT fdd.replication_set_async_execute()", "异步生效", "replication_set_async_execute", report_node="node135"),
    sql("将 node135 source_column_missing 设为 use_default_value", PORT135,
        "SELECT fdd.alter_local_node_set_conflict_resolver('source_column_missing','use_default_value')",
        "返回 node135,source_column_missing,use_default_value",
        "node135,source_column_missing,use_default_value", report_node="node135"),
    sql("确认触发前 use_default_value 冲突记录为 0", PORT135,
        "SELECT count(*) FROM fdd.mmr_conflict_history WHERE relname='%s' AND conflict_type='source_column_missing' AND conflict_resolution='use_default_value'" % TABLE,
        "返回 0", "0", report_node="node135"),
    sql("按文档插入缺少 city 的 512 行事务", PORT134,
        "BEGIN; INSERT INTO %s(id,name) SELECT gs,repeat('a',128) FROM generate_series(100001,100512) gs; COMMIT" % TABLE,
        "返回 BEGIN、INSERT 0 512、COMMIT", "BEGIN", "INSERT 0 512", "COMMIT", report_node="node134"),
    wait_for_rows("确认 node135 使用默认 city 写入 512 行并记录冲突", PORT135,
                  "SELECT (SELECT count(*) FROM %s WHERE id BETWEEN 100001 AND 100512 AND city='changsha')::text || '|' || (SELECT count(*) FROM fdd.mmr_conflict_history WHERE relname='%s' AND conflict_type='source_column_missing' AND conflict_resolution='use_default_value')::text" %
                  (TABLE, TABLE), "512|512", "node135"),
]
