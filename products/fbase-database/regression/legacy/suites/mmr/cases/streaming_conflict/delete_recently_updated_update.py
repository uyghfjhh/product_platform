"""Streaming conflict document 2.6.3: delete_recently_updated with update."""

from copy import deepcopy

from suites.mmr.cases.streaming_conflict.delete_recently_updated_error import (
    CASE as ERROR_CASE,
)
from suites.mmr.cases.streaming_conflict.delete_recently_updated_skip import (
    PORT134, PORT135, ordered_delete_after_update, sql, wait_for_rows,
)
START, END = 48001, 48512

CASE = deepcopy(ERROR_CASE)
CASE.update({
    "id": "mmr.streaming_conflict.delete_recently_updated_update",
    "name": "streaming delete_recently_updated 的 update 策略",
    "section": "2.6.3",
    "conflict_configuration": [
        "发布端 node134：debug_logical_replication_streaming=immediate，logical_decoding_work_mem=64kB。",
        "订阅端 node135：debug_logical_replication_streaming=buffered，logical_decoding_work_mem=64kB。",
        "两端 MMR streaming=parallel；node134 two_phase=false，node135 two_phase=true。",
        "本节 resolver：node135 的 delete_recently_updated=update；先按 2.6.2 重建 error 回放前态。",
    ],
})

# 2.6.3 explicitly waits for the apply worker restarted after 2.6.2 error.
# Keep the inherited shared topology and reset fixture: the inherited steps
# first recreate 2.6.2's error state, then exercise the second 512-row batch.
# Removing the session here leaves ports 15651/15652 without PostgreSQL nodes.
CASE["session"]["order"] = 170
CASE["steps"] = CASE["steps"] + [
    sql("按文档将 node135 delete_recently_updated 改为 update", PORT135,
        "SELECT fdd.alter_local_node_set_conflict_resolver('delete_recently_updated','update')",
        "返回 node135,delete_recently_updated,update",
        "node135,delete_recently_updated,update", report_node="node135"),
    wait_for_rows("确认 apply worker 重试后已有 512 条 update 冲突记录", PORT135,
                  "SELECT count(*) FROM fdd.mmr_conflict_history WHERE relname='immediate_parallel_conflict' AND conflict_type='delete_recently_updated' AND conflict_resolution='update'",
                  "512", "node135"),
    sql("按文档在 node134 插入第二批 512 行前置数据", PORT134,
        "INSERT INTO immediate_parallel_conflict(id,name) SELECT gs,repeat('a',128) FROM generate_series(%s,%s) gs" %
        (START, END),
        "返回 INSERT 0 512", "INSERT 0 512", report_node="node134"),
    wait_for_rows("等待 node135 收到第二批 512 行", PORT135,
                  "SELECT count(*) FROM immediate_parallel_conflict WHERE id BETWEEN %s AND %s" %
                  (START, END), "512", "node135"),
    ordered_delete_after_update("按 mmr-autotest 时序执行第二批 node135 UPDATE 与 node134 DELETE", START, END),
    wait_for_rows("确认 node135 第二批被删除且累计 1024 条 update 冲突记录", PORT135,
                  "SELECT (SELECT count(*) FROM immediate_parallel_conflict WHERE id BETWEEN %s AND %s)::text || '|' || (SELECT count(*) FROM fdd.mmr_conflict_history WHERE relname='immediate_parallel_conflict' AND conflict_type='delete_recently_updated' AND conflict_resolution='update')::text" %
                  (START, END), "0|1024", "node135"),
]
