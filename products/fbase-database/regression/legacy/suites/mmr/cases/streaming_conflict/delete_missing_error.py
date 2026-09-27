"""Streaming conflict document 2.1.2: delete_missing with error."""

from copy import deepcopy

from suites.mmr.cases.streaming_conflict.delete_missing_skip import (
    CASE as SKIP_CASE, PORT134, PORT135, sql, wait_for_rows,
)
from suites.mmr.cases.streaming_conflict.update_missing_insert_or_skip import (
    shared_non2pc_set_steps,
)


CASE = deepcopy(SKIP_CASE)
CASE.update({
    "id": "mmr.streaming_conflict.delete_missing_error",
    "name": "streaming delete_missing 的 error 策略",
    "section": "2.1.2",
    "conflict_configuration": [
        "发布端 node134：debug_logical_replication_streaming=immediate，logical_decoding_work_mem=64kB。",
        "订阅端 node135：debug_logical_replication_streaming=buffered，logical_decoding_work_mem=64kB。",
        "两端 MMR streaming=parallel；node134 two_phase=false，node135 two_phase=true。",
        "本节 resolver：node135 的 delete_missing=error；回放事务应保留 512 行并记录冲突。",
    ],
})
CASE["session"]["order"] = 100

# The named common setup ends at the empty table verification.  The error
# scenario then follows 2.1.2's own resolver, id range, and final effect.
base_steps = shared_non2pc_set_steps()
CASE["steps"] = base_steps + [
    sql("将 node135 delete_missing 设为 error", PORT135,
        "SELECT fdd.alter_local_node_set_conflict_resolver('delete_missing','error')",
        "返回 node135,delete_missing,error", "node135,delete_missing,error",
        report_node="node135"),
    sql("确认触发前 error 冲突记录为 0", PORT135,
        "SELECT count(*) FROM fdd.mmr_conflict_history WHERE relname='immediate_parallel_conflict' AND conflict_type='delete_missing' AND conflict_resolution='error'",
        "返回 0", "0", report_node="node135"),
    sql("按文档在 node134 插入 512 行大事务", PORT134,
        "INSERT INTO immediate_parallel_conflict(id,name) SELECT gs,repeat('a',128) FROM generate_series(99001,99512) gs",
        "返回 INSERT 0 512", "INSERT 0 512", report_node="node134"),
    wait_for_rows("等待 node135 收到 512 行", PORT135,
                  "SELECT count(*) FROM immediate_parallel_conflict WHERE id BETWEEN 99001 AND 99512",
                  "512", "node135"),
    sql("按文档删除缺失 id=99 和 512 行", PORT134,
        "BEGIN; DELETE FROM immediate_parallel_conflict WHERE id=99 OR id BETWEEN 99001 AND 99512; COMMIT",
        "返回 BEGIN、DELETE 513、COMMIT", "BEGIN", "DELETE 513", "COMMIT",
        report_node="node134"),
    sql("确认 node134 的 513 个目标行均已删除", PORT134,
        "SELECT count(*) FROM immediate_parallel_conflict WHERE id=99 OR id BETWEEN 99001 AND 99512",
        "返回 0", "0", report_node="node134"),
    wait_for_rows("确认 node135 保留 512 行并记录 error 冲突", PORT135,
                  "SELECT (SELECT count(*) FROM immediate_parallel_conflict WHERE id=99 OR id BETWEEN 99001 AND 99512)::text || '|' || (SELECT count(*) FROM fdd.mmr_conflict_history WHERE relname='immediate_parallel_conflict' AND conflict_type='delete_missing' AND conflict_resolution='error')::text",
                  "512|1", "node135"),
]
