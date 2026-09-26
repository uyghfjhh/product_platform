"""Streaming conflict document 2.2.2: update_missing with error."""

from copy import deepcopy

from suites.mmr.cases.streaming_conflict.update_missing_insert_or_skip import (
    CASE as BASE_CASE, shared_update_missing_steps,
)
from suites.mmr.cases.streaming_conflict.delete_missing_skip import PORT134, PORT135, sql, wait_for_rows


CASE = deepcopy(BASE_CASE)
CASE.update({"id": "mmr.streaming_conflict.update_missing_error",
             "name": "streaming update_missing 的 error 策略", "section": "2.2.2"})
CASE["session"]["order"] = 30
CASE["conflict_configuration"][-1] = "本节 resolver：node135 的 update_missing=error；回放事务不插入缺失 id=89。"
CASE["steps"] = shared_update_missing_steps() + [
    sql("将 node135 update_missing 设为 error", PORT135,
        "SELECT fdd.alter_local_node_set_conflict_resolver('update_missing','error')",
        "返回 node135,update_missing,error", "node135,update_missing,error", report_node="node135"),
    sql("确认触发前 error 冲突记录为 0", PORT135,
        "SELECT count(*) FROM fdd.mmr_conflict_history WHERE relname='immediate_parallel_conflict' AND conflict_type='update_missing' AND conflict_resolution='error'",
        "返回 0", "0", report_node="node135"),
    sql("按文档更新目标缺失 id=89 和 512 行", PORT134,
        "BEGIN; UPDATE immediate_parallel_conflict SET name=repeat('c',128) WHERE id=89 OR id BETWEEN 90001 AND 90512; COMMIT",
        "返回 BEGIN、UPDATE 513、COMMIT", "BEGIN", "UPDATE 513", "COMMIT", report_node="node134"),
    wait_for_rows("确认 node135 不插入 id=89 并记录 error 冲突", PORT135,
        "SELECT (SELECT count(*) FROM immediate_parallel_conflict WHERE id=89)::text || '|' || (SELECT count(*) FROM fdd.mmr_conflict_history WHERE relname='immediate_parallel_conflict' AND conflict_type='update_missing' AND conflict_resolution='error')::text",
        "0|1", "node135"),
]
