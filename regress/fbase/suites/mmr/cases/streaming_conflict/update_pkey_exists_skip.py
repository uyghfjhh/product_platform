"""Streaming conflict document 2.4.3: update_pkey_exists with skip."""

from copy import deepcopy

from suites.mmr.cases.streaming_conflict.delete_missing_skip import (
    PORT134, PORT135, sql, wait_for_rows,
)
from suites.mmr.cases.streaming_conflict.update_pkey_exists_error import (
    CASE as ERROR_CASE,
)
from suites.mmr.cases.streaming_conflict.update_missing_insert_or_skip import (
    common_streaming_set_steps,
)


CASE = deepcopy(ERROR_CASE)
CASE.update({
    "id": "mmr.streaming_conflict.update_pkey_exists_skip",
    "name": "streaming update_pkey_exists 的 skip 策略",
    "section": "2.4.3",
    "conflict_configuration": [
        "发布端/订阅端：debug_logical_replication_streaming 分别为 immediate/buffered，logical_decoding_work_mem=64kB。",
        "两端 MMR streaming=parallel；node134 two_phase=false，node135 two_phase=true。",
        "本节 resolver：node135 的 update_pkey_exists=skip；先按 2.4.2 重建 error 回放前态。",
    ],
})

# The document explicitly waits for the worker restarted after the preceding
# error, then observes 512 skip records before the next concurrent update.
CASE.pop("session", None)
CASE["fixtures"] = [
    "cluster",
    {"type": "isolated_mmr_node_creation",
     "data_dir": "/tmp/fbase_regress_mmr_stream_delete_missing_{run_id}"},
]
CASE["steps"] = common_streaming_set_steps() + deepcopy(ERROR_CASE["steps"][-6:]) + [
    sql("按文档将 node135 update_pkey_exists 改为 skip", PORT135,
        "SELECT fdd.alter_local_node_set_conflict_resolver('update_pkey_exists','skip')",
        "返回 node135,update_pkey_exists,skip", "node135,update_pkey_exists,skip",
        report_node="node135"),
    wait_for_rows("确认 apply worker 重试后已有 512 条 skip 冲突记录", PORT135,
                  "SELECT count(*) FROM fdd.mmr_conflict_history WHERE relname='immediate_parallel_conflict' AND conflict_type='update_pkey_exists' AND conflict_resolution='skip'",
                  "512", "node135"),
    sql("按文档在 node134 插入第二批 512 行主键更新前置数据", PORT134,
        "INSERT INTO immediate_parallel_conflict(id,name) SELECT gs,repeat('a',128) FROM generate_series(68001,68512) gs",
        "返回 INSERT 0 512", "INSERT 0 512", report_node="node134"),
    wait_for_rows("等待 node135 收到第二批 512 行", PORT135,
                  "SELECT count(*) FROM immediate_parallel_conflict WHERE id BETWEEN 68001 AND 68512",
                  "512", "node135"),
    sql("按文档在所有节点同时更新第二批 512 个主键", PORT134,
        "SELECT fdd.run_on_all_nodes('update immediate_parallel_conflict set id=id+1000000 where id between 68001 and 68512')",
        "node134/node135 均返回 UPDATE 512", "node134", "node135", "UPDATE 512",
        report_node="node134"),
    wait_for_rows("确认 node135 第二批旧主键清空、新主键为 512 行且累计 1024 条 skip", PORT135,
                  "SELECT (SELECT count(*) FROM immediate_parallel_conflict WHERE id BETWEEN 68001 AND 68512)::text || '|' || (SELECT count(*) FROM immediate_parallel_conflict WHERE id BETWEEN 1068001 AND 1068512)::text || '|' || (SELECT count(*) FROM fdd.mmr_conflict_history WHERE relname='immediate_parallel_conflict' AND conflict_type='update_pkey_exists' AND conflict_resolution='skip')::text",
                  "0|512|1024", "node135"),
]
