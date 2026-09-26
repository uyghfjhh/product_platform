"""Streaming conflict document 2.9.1: target_table_missing skip_if_recently_dropped."""

from suites.mmr.cases.streaming_conflict.delete_missing_skip import (
    PORT134, PORT135, sql, wait_for_rows,
)
from suites.mmr.cases.streaming_conflict.update_missing_insert_or_skip import common_streaming_set_steps
from suites.mmr.streaming_conflict_support import non2pc_case_base


TABLE = "immediate_parallel_drop"

CASE = non2pc_case_base(
    "mmr.streaming_conflict.target_table_missing_skip_if_recently_dropped",
    "streaming target_table_missing 的 skip_if_recently_dropped 策略", "2.9.1", [
        "发布端 node134：debug_logical_replication_streaming=immediate，logical_decoding_work_mem=64kB。",
        "订阅端 node135：debug_logical_replication_streaming=buffered，logical_decoding_work_mem=64kB。",
        "两端 MMR streaming=parallel；node134 two_phase=false，node135 two_phase=true。",
        "所有节点订阅默认复制集 g1；node135 删除本地表后 target_table_missing=skip_if_recently_dropped。",
    ])
CASE["steps"] = common_streaming_set_steps() + [
    sql("按文档在所有节点订阅默认复制集 g1", PORT134,
        "SELECT fdd.run_on_all_nodes('select fdd.alter_node_replication_sets(''{g1}'');')",
        "node134/node135 均成功", "node134", "node135", report_node="node134"),
    sql("按文档在所有节点创建目标表缺失测试表", PORT134,
        "SELECT fdd.run_on_all_nodes('create table %s(id int primary key,name text);')" % TABLE,
        "node134/node135 均创建成功", "node134", "node135", "CREATE TABLE", report_node="node134"),
    sql("按文档在 node135 异步执行默认复制集变更", PORT135,
        "SELECT fdd.replication_set_async_execute()", "异步生效", "replication_set_async_execute", report_node="node135"),
    sql("按文档在 node135 删除本地测试表", PORT135,
        "DROP TABLE %s" % TABLE, "返回 DROP TABLE", "DROP TABLE", report_node="node135"),
    sql("将 node135 target_table_missing 设为 skip_if_recently_dropped", PORT135,
        "SELECT fdd.alter_local_node_set_conflict_resolver('target_table_missing','skip_if_recently_dropped')",
        "返回 node135,target_table_missing,skip_if_recently_dropped",
        "node135,target_table_missing,skip_if_recently_dropped", report_node="node135"),
    sql("确认触发前 skip_if_recently_dropped 冲突记录为 0", PORT135,
        "SELECT count(*) FROM fdd.mmr_conflict_history WHERE relname='%s' AND conflict_type='target_table_missing' AND conflict_resolution='skip_if_recently_dropped'" % TABLE,
        "返回 0", "0", report_node="node135"),
    sql("按文档在 node134 插入 512 行事务", PORT134,
        "BEGIN; INSERT INTO %s(id,name) SELECT gs,repeat('a',128) FROM generate_series(100001,100512) gs; COMMIT" % TABLE,
        "返回 BEGIN、INSERT 0 512、COMMIT", "BEGIN", "INSERT 0 512", "COMMIT", report_node="node134"),
    wait_for_rows("确认 node135 记录 512 条 skip_if_recently_dropped 历史", PORT135,
                  "SELECT count(*) FROM fdd.mmr_conflict_history WHERE relname='%s' AND conflict_type='target_table_missing' AND conflict_resolution='skip_if_recently_dropped'" % TABLE,
                  "512", "node135"),
]
