"""Streaming conflict document 2.7.4: target_column_missing with ignore."""

from copy import deepcopy

from suites.mmr.cases.streaming_conflict.delete_missing_skip import (
    PORT134, PORT135, sql, wait_for_rows,
)
from suites.mmr.cases.streaming_conflict.target_column_missing_error import (
    CASE as ERROR_CASE,
)
from suites.mmr.cases.streaming_conflict.target_column_missing_ignore_if_null import TABLE


CASE = deepcopy(ERROR_CASE)
CASE.update({
    "id": "mmr.streaming_conflict.target_column_missing_ignore",
    "name": "streaming target_column_missing 的 ignore 策略",
    "section": "2.7.4",
    "conflict_configuration": [
        "发布端 node134：debug_logical_replication_streaming=immediate，logical_decoding_work_mem=64kB。",
        "订阅端 node135：debug_logical_replication_streaming=buffered，logical_decoding_work_mem=64kB。",
        "两端 MMR streaming=parallel；node134 two_phase=false，node135 two_phase=true。",
        "node135 的 target_column_missing=ignore；先按 2.7.3 重建 error 回放前态。",
    ],
})
CASE.pop("session", None)
CASE["fixtures"] = [
    "cluster",
    {"type": "isolated_mmr_node_creation",
     "data_dir": "/tmp/fbase_regress_mmr_stream_delete_missing_{run_id}"},
]
CASE["steps"] = CASE["steps"] + [
    sql("按文档将 node135 target_column_missing 改为 ignore", PORT135,
        "SELECT fdd.alter_local_node_set_conflict_resolver('target_column_missing','ignore')",
        "返回 node135,target_column_missing,ignore", "node135,target_column_missing,ignore",
        report_node="node135"),
    wait_for_rows("确认 apply worker 重试后第一批已同步且有 512 条 ignore 历史", PORT135,
                  "SELECT (SELECT count(*) FROM %s WHERE id BETWEEN 98001 AND 98512)::text || '|' || (SELECT count(*) FROM fdd.mmr_conflict_history WHERE relname='%s' AND conflict_type='target_column_missing' AND conflict_resolution='ignore')::text" %
                  (TABLE, TABLE), "512|512", "node135"),
    sql("按文档插入第二批带 city 的 512 行事务", PORT134,
        "BEGIN; INSERT INTO %s(id,name,city) SELECT gs,repeat('a',128),'changsha' FROM generate_series(97001,97512) gs; COMMIT" % TABLE,
        "返回 BEGIN、INSERT 0 512、COMMIT", "BEGIN", "INSERT 0 512", "COMMIT",
        report_node="node134"),
    wait_for_rows("确认 node135 接收第二批且累计 1024 条 ignore 历史", PORT135,
                  "SELECT (SELECT count(*) FROM %s WHERE id BETWEEN 97001 AND 97512)::text || '|' || (SELECT count(*) FROM fdd.mmr_conflict_history WHERE relname='%s' AND conflict_type='target_column_missing' AND conflict_resolution='ignore')::text" %
                  (TABLE, TABLE), "512|1024", "node135"),
]
