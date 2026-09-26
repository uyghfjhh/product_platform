"""Streaming conflict document 2.3.3: insert_exists with skip."""

from copy import deepcopy

from suites.mmr.cases.streaming_conflict.delete_missing_skip import (
    PORT134, PORT135, sql, wait_for_rows,
)
from suites.mmr.cases.streaming_conflict.insert_exists_error import CASE as ERROR_CASE
from suites.mmr.cases.streaming_conflict.update_missing_insert_or_skip import (
    common_streaming_set_steps,
)


CASE = deepcopy(ERROR_CASE)
CASE.update({
    "id": "mmr.streaming_conflict.insert_exists_skip",
    "name": "streaming insert_exists 的 skip 策略",
    "section": "2.3.3",
    "conflict_configuration": [
        "发布端 node135：debug_logical_replication_streaming=buffered，logical_decoding_work_mem=64kB。",
        "订阅端 node134：debug_logical_replication_streaming=immediate，logical_decoding_work_mem=64kB。",
        "两端 MMR streaming=parallel；node134 two_phase=false，node135 two_phase=true。",
        "本节 resolver：node134 的 insert_exists=skip；先按 2.3.2 重建 error 回放前态。",
    ],
})

# 2.3.3 explicitly observes the apply worker after the preceding error.  Keep
# that complete, standalone 2.3.2 state before changing the resolver.
CASE.pop("session", None)
CASE["fixtures"] = [
    "cluster",
    {"type": "isolated_mmr_node_creation",
     "data_dir": "/tmp/fbase_regress_mmr_stream_delete_missing_{run_id}"},
]
CASE["steps"] = common_streaming_set_steps() + deepcopy(ERROR_CASE["steps"][-4:])
CASE["steps"][-1]["continue_on_failure"] = True
CASE["steps"] = CASE["steps"] + [
    sql("按文档将 node134 insert_exists 改为 skip", PORT134,
        "SELECT fdd.alter_local_node_set_conflict_resolver('insert_exists','skip')",
        "返回 node134,insert_exists,skip", "node134,insert_exists,skip",
        report_node="node134"),
    wait_for_rows("确认 apply worker 重试后已有一条 skip 冲突记录", PORT134,
                  "SELECT count(*) FROM fdd.mmr_conflict_history WHERE relname='immediate_parallel_conflict' AND conflict_type='insert_exists' AND conflict_resolution='skip'",
                  "1", "node134"),
    sql("按文档在 node135 插入冲突 id=78 和第二批 512 行", PORT135,
        "BEGIN; INSERT INTO immediate_parallel_conflict(id,name) SELECT gs,repeat('b',128) FROM generate_series(78,78512) gs WHERE gs=78 OR gs BETWEEN 78001 AND 78512; COMMIT",
        "返回 BEGIN、INSERT 0 513、COMMIT", "BEGIN", "INSERT 0 513", "COMMIT",
        report_node="node135"),
    wait_for_rows("确认 node134 接收第二批 512 行、保留 id=78 且有两条 skip 记录", PORT134,
                  "SELECT (SELECT count(*) FROM immediate_parallel_conflict WHERE id BETWEEN 78001 AND 78512)::text || '|' || (SELECT count(*) FROM immediate_parallel_conflict WHERE id=78 AND name='a')::text || '|' || (SELECT count(*) FROM fdd.mmr_conflict_history WHERE relname='immediate_parallel_conflict' AND conflict_type='insert_exists' AND conflict_resolution='skip')::text",
                  "512|1|2", "node134"),
]
