"""Streaming conflict document 2.2.3: update_missing with skip."""

from copy import deepcopy

from suites.mmr.cases.streaming_conflict.update_missing_error import CASE as ERROR_CASE
from suites.mmr.cases.streaming_conflict.update_missing_insert_or_skip import (
    common_update_missing_steps,
)
from suites.mmr.cases.streaming_conflict.delete_missing_skip import (
    PORT134, PORT135, sql, wait_for_rows,
)


CASE = deepcopy(ERROR_CASE)
CASE.update({
    "id": "mmr.streaming_conflict.update_missing_skip",
    "name": "streaming update_missing 的 skip 策略",
    "section": "2.2.3",
    "conflict_configuration": [
        "发布端 node134：debug_logical_replication_streaming=immediate，logical_decoding_work_mem=64kB。",
        "订阅端 node135：debug_logical_replication_streaming=buffered，logical_decoding_work_mem=64kB。",
        "两端 MMR streaming=parallel；node134 two_phase=false，node135 two_phase=true。",
        "本节 resolver：node135 的 update_missing=skip；先按 2.2.2 重建 error 回放前态。",
    ],
})

# This scenario relies on an error worker dying and replaying its original
# transaction.  Reusing the session subscription changes that retry state.
CASE.pop("session", None)
CASE["fixtures"] = [
    "cluster",
    {"type": "isolated_mmr_node_creation",
     "data_dir": "/tmp/fbase_regress_mmr_stream_delete_missing_{run_id}"},
]
CASE["steps"] = (common_update_missing_steps() +
                   deepcopy(ERROR_CASE["steps"][-4:]) + [
    sql("按文档将 node135 update_missing 改为 skip", PORT135,
        "SELECT fdd.alter_local_node_set_conflict_resolver('update_missing','skip')",
        "返回 node135,update_missing,skip", "node135,update_missing,skip",
        report_node="node135"),
    wait_for_rows("确认 apply worker 重试后已有一条 skip 冲突记录", PORT135,
                  "SELECT count(*) FROM fdd.mmr_conflict_history WHERE relname='immediate_parallel_conflict' AND conflict_type='update_missing' AND conflict_resolution='skip'",
                  "1", "node135"),
    sql("按文档更新目标缺失 id=88 和 512 行", PORT134,
        "BEGIN; UPDATE immediate_parallel_conflict SET name=repeat('d',128) WHERE id=88 OR id BETWEEN 90001 AND 90512; COMMIT",
        "返回 BEGIN、UPDATE 513、COMMIT", "BEGIN", "UPDATE 513", "COMMIT",
        report_node="node134"),
    wait_for_rows("确认 node135 不插入 id=88 且有两条 skip 冲突记录", PORT135,
                  "SELECT (SELECT count(*) FROM immediate_parallel_conflict WHERE id=88)::text || '|' || (SELECT count(*) FROM fdd.mmr_conflict_history WHERE relname='immediate_parallel_conflict' AND conflict_type='update_missing' AND conflict_resolution='skip')::text",
                  "0|2", "node135"),
])
