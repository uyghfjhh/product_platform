"""Streaming conflict document 2.1.3: delete_missing with skip_transaction."""

from copy import deepcopy

from suites.mmr.cases.streaming_conflict.delete_missing_error import (
    CASE as ERROR_CASE,
)
from suites.mmr.cases.streaming_conflict.delete_missing_skip import (
    PORT134, PORT135, sql, wait_for_rows,
)
from suites.mmr.cases.streaming_conflict.update_missing_insert_or_skip import (
    common_streaming_set_steps,
)


CASE = deepcopy(ERROR_CASE)
CASE.update({
    "id": "mmr.streaming_conflict.delete_missing_skip_transaction",
    "name": "streaming delete_missing 的 skip_transaction 策略",
    "section": "2.1.3",
    "conflict_configuration": [
        "发布端 node134：debug_logical_replication_streaming=immediate，logical_decoding_work_mem=64kB。",
        "订阅端 node135：debug_logical_replication_streaming=buffered，logical_decoding_work_mem=64kB。",
        "两端 MMR streaming=parallel；node134 two_phase=false，node135 two_phase=true。",
        "本节 resolver：node135 的 delete_missing=skip_transaction；先按 2.1.2 重建 error 回放前态。",
    ],
    "prerequisites": [
        "严格按文档使用临时 two_phase 双节点，不触碰共享 mmr。",
        "先完整重建 2.1.2 的 error 前态，使 node135 保留 99001-99512 的 512 行并已有一条冲突记录。",
    ],
})

# ERROR_CASE has a complete standalone 2.1.2 prerequisite through its final
# 512|1 assertion.  2.1.3 starts from that exact documented state.
CASE.pop("session", None)
CASE["fixtures"] = [
    "cluster",
    {"type": "isolated_mmr_node_creation",
     "data_dir": "/tmp/fbase_regress_mmr_stream_delete_missing_{run_id}"},
]
CASE["steps"] = common_streaming_set_steps() + deepcopy(ERROR_CASE["steps"][-7:]) + [
    sql("按文档将 node135 delete_missing 改为 skip_transaction", PORT135,
        "SELECT fdd.alter_local_node_set_conflict_resolver('delete_missing','skip_transaction')",
        "返回 node135,delete_missing,skip_transaction",
        "node135,delete_missing,skip_transaction", report_node="node135"),
    wait_for_rows("确认 apply worker 重试后已有一条 skip_transaction 冲突记录", PORT135,
                  "SELECT count(*) FROM fdd.mmr_conflict_history WHERE relname='immediate_parallel_conflict' AND conflict_type='delete_missing' AND conflict_resolution='skip_transaction'",
                  "1", "node135"),
    sql("按文档在 node134 插入第二批 512 行大事务", PORT134,
        "INSERT INTO immediate_parallel_conflict(id,name) SELECT gs,repeat('a',128) FROM generate_series(98001,98512) gs",
        "返回 INSERT 0 512", "INSERT 0 512", report_node="node134"),
    wait_for_rows("等待 node135 收到第二批 512 行", PORT135,
                  "SELECT count(*) FROM immediate_parallel_conflict WHERE id BETWEEN 98001 AND 98512",
                  "512", "node135"),
    sql("按文档删除缺失 id=98 和第二批 512 行", PORT134,
        "BEGIN; DELETE FROM immediate_parallel_conflict WHERE id=98 OR id BETWEEN 98001 AND 98512; COMMIT",
        "返回 BEGIN、DELETE 513、COMMIT", "BEGIN", "DELETE 513", "COMMIT",
        report_node="node134"),
    sql("确认 node134 的第二批 513 个目标行均已删除", PORT134,
        "SELECT count(*) FROM immediate_parallel_conflict WHERE id=98 OR id BETWEEN 98001 AND 98512",
        "返回 0", "0", report_node="node134"),
    wait_for_rows("确认 node135 保留第二批 512 行且有两条 skip_transaction 记录", PORT135,
                  "SELECT (SELECT count(*) FROM immediate_parallel_conflict WHERE id=98 OR id BETWEEN 98001 AND 98512)::text || '|' || (SELECT count(*) FROM fdd.mmr_conflict_history WHERE relname='immediate_parallel_conflict' AND conflict_type='delete_missing' AND conflict_resolution='skip_transaction')::text",
                  "512|2", "node135"),
]
