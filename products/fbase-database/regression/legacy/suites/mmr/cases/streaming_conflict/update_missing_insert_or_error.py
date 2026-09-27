"""Streaming conflict document 2.2.4: update_missing with insert_or_error."""

from copy import deepcopy

from suites.mmr.cases.streaming_conflict.update_missing_insert_or_skip import (
    CASE as BASE_CASE, shared_update_missing_steps,
)
from suites.mmr.cases.streaming_conflict.delete_missing_skip import PORT134, PORT135, sql, wait_for_rows


CASE = deepcopy(BASE_CASE)
CASE.update({"id": "mmr.streaming_conflict.update_missing_insert_or_error",
             "name": "streaming update_missing 的 insert_or_error 策略", "section": "2.2.4"})
CASE["session"]["order"] = 20
CASE["conflict_configuration"][-1] = "本节 resolver：node135 的 update_missing=insert_or_error。"
CASE["steps"] = shared_update_missing_steps() + [
    sql("将 node135 update_missing 设为 insert_or_error", PORT135,
        "SELECT fdd.alter_local_node_set_conflict_resolver('update_missing','insert_or_error')",
        "返回 node135,update_missing,insert_or_error", "node135,update_missing,insert_or_error", report_node="node135"),
    sql("确认触发前 insert_or_error 冲突记录为 0", PORT135,
        "SELECT count(*) FROM fdd.mmr_conflict_history WHERE relname='immediate_parallel_conflict' AND conflict_type='update_missing' AND conflict_resolution='insert_or_error'",
        "返回 0", "0", report_node="node135"),
    sql("按文档更新目标缺失 id=87 和 512 行", PORT134,
        "BEGIN; UPDATE immediate_parallel_conflict SET name=repeat('e',128) WHERE id=87 OR id BETWEEN 90001 AND 90512; COMMIT",
        "返回 BEGIN、UPDATE 513、COMMIT", "BEGIN", "UPDATE 513", "COMMIT", report_node="node134"),
    wait_for_rows("确认 node135 按 insert_or_error 写入 id=87 并记录冲突", PORT135,
        "SELECT (SELECT count(*) FROM immediate_parallel_conflict WHERE id=87 AND name=repeat('e',128))::text || '|' || (SELECT count(*) FROM fdd.mmr_conflict_history WHERE relname='immediate_parallel_conflict' AND conflict_type='update_missing' AND conflict_resolution='insert_or_error')::text",
        "1|1", "node135"),
]
