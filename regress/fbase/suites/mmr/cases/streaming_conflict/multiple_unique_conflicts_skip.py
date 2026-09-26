"""Streaming conflict document 2.10.2: multiple_unique_conflicts skip."""

from copy import deepcopy

from suites.mmr.cases.streaming_conflict.delete_missing_skip import sql, wait_for_rows
from suites.mmr.cases.streaming_conflict.multiple_unique_conflicts_error import (
    CASE as ERROR_CASE, JOINER_PORT, PEER_PORT, SOURCE_PORT, TABLE,
)


CASE = deepcopy(ERROR_CASE)
CASE.update({"id": "mmr.streaming_conflict.multiple_unique_conflicts_skip",
             "name": "streaming multiple_unique_conflicts 的 skip 策略", "section": "2.10.2"})
CASE["conflict_configuration"][-1] = "node134/node135 的 multiple_unique_conflicts=skip；error 后重试同一 100 行事务时仅跳过冲突行，其余 99 行继续应用。"
CASE["steps"] += [
    sql("将 node134 multiple_unique_conflicts 改为 skip", SOURCE_PORT,
        "SELECT fdd.alter_local_node_set_conflict_resolver('multiple_unique_conflicts','skip')",
        "返回 node134,multiple_unique_conflicts,skip", "node134,multiple_unique_conflicts,skip", report_node="node134"),
    sql("将 node135 multiple_unique_conflicts 改为 skip", PEER_PORT,
        "SELECT fdd.alter_local_node_set_conflict_resolver('multiple_unique_conflicts','skip')",
        "返回 node135,multiple_unique_conflicts,skip", "node135,multiple_unique_conflicts,skip", report_node="node135"),
    wait_for_rows("确认 node134 worker 重试后有一条 skip 历史", SOURCE_PORT,
        "SELECT count(*) FROM fdd.mmr_conflict_history WHERE relname='%s' AND conflict_type='multiple_unique_conflicts' AND conflict_resolution='skip'" % TABLE, "1", "node134"),
    wait_for_rows("确认 node135 worker 重试后有一条 skip 历史", PEER_PORT,
        "SELECT count(*) FROM fdd.mmr_conflict_history WHERE relname='%s' AND conflict_type='multiple_unique_conflicts' AND conflict_resolution='skip'" % TABLE, "1", "node135"),
    sql("按文档在 node136 更新 100 行冲突数据", JOINER_PORT,
        "BEGIN; UPDATE %s SET name=repeat('c',1024); COMMIT" % TABLE,
        "返回 BEGIN、UPDATE 100、COMMIT", "BEGIN", "UPDATE 100", "COMMIT", report_node="node136"),
    wait_for_rows("确认 node134 重试写入 99 个非冲突行且累计两条 skip 历史", SOURCE_PORT,
        "SELECT (SELECT count(*) FROM %s)::text || '|' || (SELECT count(*) FROM fdd.mmr_conflict_history WHERE relname='%s' AND conflict_type='multiple_unique_conflicts' AND conflict_resolution='skip')::text" % (TABLE,TABLE), "101|2", "node134"),
    wait_for_rows("确认 node135 重试写入 99 个非冲突行且累计两条 skip 历史", PEER_PORT,
        "SELECT (SELECT count(*) FROM %s)::text || '|' || (SELECT count(*) FROM fdd.mmr_conflict_history WHERE relname='%s' AND conflict_type='multiple_unique_conflicts' AND conflict_resolution='skip')::text" % (TABLE,TABLE), "101|2", "node135"),
]
