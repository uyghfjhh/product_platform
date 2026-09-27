"""Streaming conflict document 2.9.3: target_table_missing with skip."""

from copy import deepcopy

from suites.mmr.cases.streaming_conflict.delete_missing_skip import PORT134, PORT135, sql, wait_for_rows
from suites.mmr.cases.streaming_conflict.target_table_missing_error import CASE as ERROR_CASE
from suites.mmr.cases.streaming_conflict.target_table_missing_skip_if_recently_dropped import TABLE


CASE = deepcopy(ERROR_CASE)
CASE.update({
    "id": "mmr.streaming_conflict.target_table_missing_skip",
    "name": "streaming target_table_missing 的 skip 策略", "section": "2.9.3",
    "conflict_configuration": [
        "发布端 node134：debug_logical_replication_streaming=immediate，logical_decoding_work_mem=64kB。",
        "订阅端 node135：debug_logical_replication_streaming=buffered，logical_decoding_work_mem=64kB。",
        "两端 MMR streaming=parallel；node134 two_phase=false，node135 two_phase=true。",
        "node135 的 target_table_missing=skip；先按 2.9.2 重建 error 回放前态。",
    ],
})
CASE["steps"] += [
    sql("按文档将 node135 target_table_missing 改为 skip", PORT135,
        "SELECT fdd.alter_local_node_set_conflict_resolver('target_table_missing','skip')",
        "返回 node135,target_table_missing,skip", "node135,target_table_missing,skip", report_node="node135"),
    wait_for_rows("确认 apply worker 重试后已有 512 条 skip 历史", PORT135,
                  "SELECT count(*) FROM fdd.mmr_conflict_history WHERE relname='%s' AND conflict_type='target_table_missing' AND conflict_resolution='skip'" % TABLE,
                  "512", "node135"),
    sql("按文档在 node134 插入第二批 512 行事务", PORT134,
        "BEGIN; INSERT INTO %s(id,name) SELECT gs,repeat('a',128) FROM generate_series(98001,98512) gs; COMMIT" % TABLE,
        "返回 BEGIN、INSERT 0 512、COMMIT", "BEGIN", "INSERT 0 512", "COMMIT", report_node="node134"),
    wait_for_rows("确认 node135 累计 1024 条 skip 历史", PORT135,
                  "SELECT count(*) FROM fdd.mmr_conflict_history WHERE relname='%s' AND conflict_type='target_table_missing' AND conflict_resolution='skip'" % TABLE,
                  "1024", "node135"),
]
