"""Streaming conflict document 2.2.5: update_missing with skip_transaction."""

from copy import deepcopy

from suites.mmr.cases.streaming_conflict.update_missing_insert_or_skip import (
    CASE as BASE_CASE, shared_update_missing_steps,
)
from suites.mmr.cases.streaming_conflict.delete_missing_skip import (
    PORT134, PORT135, sql, wait_for_rows,
)


CASE = deepcopy(BASE_CASE)
CASE.update({
    "id": "mmr.streaming_conflict.update_missing_skip_transaction",
    "name": "streaming update_missing 的 skip_transaction 策略",
    "section": "2.2.5",
    "conflict_configuration": [
        "发布端 node134：debug_logical_replication_streaming=immediate，logical_decoding_work_mem=64kB。",
        "订阅端 node135：debug_logical_replication_streaming=buffered，logical_decoding_work_mem=64kB。",
        "两端 MMR streaming=parallel；node134 two_phase=false，node135 two_phase=true。",
        "本节 resolver：node135 的 update_missing=skip_transaction；回放事务不插入缺失 id=86。",
    ],
})
CASE["session"]["order"] = 40

# Reuse only the documented common setup through node135's empty-table check.
# The resolver and 513-row transaction below are 2.2.5-specific evidence.
CASE["steps"] = shared_update_missing_steps() + [
    sql("将 node135 update_missing 设为 skip_transaction", PORT135,
        "SELECT fdd.alter_local_node_set_conflict_resolver('update_missing','skip_transaction')",
        "返回 node135,update_missing,skip_transaction",
        "node135,update_missing,skip_transaction", report_node="node135"),
    sql("确认触发前 skip_transaction 冲突记录为 0", PORT135,
        "SELECT count(*) FROM fdd.mmr_conflict_history WHERE relname='immediate_parallel_conflict' AND conflict_type='update_missing' AND conflict_resolution='skip_transaction'",
        "返回 0", "0", report_node="node135"),
    sql("按文档更新目标缺失 id=86 和 512 行", PORT134,
        "BEGIN; UPDATE immediate_parallel_conflict SET name=repeat('f',128) WHERE id=86 OR id BETWEEN 90001 AND 90512; COMMIT",
        "返回 BEGIN、UPDATE 513、COMMIT", "BEGIN", "UPDATE 513", "COMMIT",
        report_node="node134"),
    wait_for_rows("确认 node135 不插入 id=86 并记录 skip_transaction 冲突", PORT135,
                  "SELECT (SELECT count(*) FROM immediate_parallel_conflict WHERE id=86)::text || '|' || (SELECT count(*) FROM fdd.mmr_conflict_history WHERE relname='immediate_parallel_conflict' AND conflict_type='update_missing' AND conflict_resolution='skip_transaction')::text",
                  "0|1", "node135"),
]
