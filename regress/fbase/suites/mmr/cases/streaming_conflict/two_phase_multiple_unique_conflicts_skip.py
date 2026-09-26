"""Streaming conflict document 3.10.2: 2PC multiple_unique_conflicts/skip."""

from copy import deepcopy

from suites.mmr.cases.streaming_conflict.delete_missing_skip import sql, wait_for_rows
from suites.mmr.cases.streaming_conflict.two_phase_multiple_unique_conflicts_error import (
    CASE as ERROR_CASE, JOINER_PORT, PEER_PORT, SOURCE_PORT, TABLE,
)

CASE = deepcopy(ERROR_CASE)
CASE.update({"id": "mmr.streaming_conflict.two_phase_multiple_unique_conflicts_skip",
             "name": "2PC streaming multiple_unique_conflicts 的 skip 策略",
             "section": "3.10.2"})
CASE["steps"] += [
    sql("按文档将 node134 multiple_unique_conflicts 改为 skip", SOURCE_PORT,
        "SELECT fdd.alter_local_node_set_conflict_resolver('multiple_unique_conflicts','skip')",
        "返回 node134,multiple_unique_conflicts,skip", "node134,multiple_unique_conflicts,skip", report_node="node134"),
    sql("按文档将 node135 multiple_unique_conflicts 改为 skip", PEER_PORT,
        "SELECT fdd.alter_local_node_set_conflict_resolver('multiple_unique_conflicts','skip')",
        "返回 node135,multiple_unique_conflicts,skip", "node135,multiple_unique_conflicts,skip", report_node="node135"),
    wait_for_rows("确认 node135 worker 重试后有一条 skip 历史", PEER_PORT,
        "SELECT count(*) FROM fdd.mmr_conflict_history WHERE relname='%s' AND conflict_type='multiple_unique_conflicts' AND conflict_resolution='skip'" % TABLE, "1", "node135"),
    sql("按文档在 node136 准备第一次 100 行更新事务", JOINER_PORT,
        "BEGIN; UPDATE %s SET name=repeat('c',1024); PREPARE TRANSACTION 'stream_c'" % TABLE,
        "返回 BEGIN、UPDATE 100、PREPARE TRANSACTION", "BEGIN", "UPDATE 100", "PREPARE TRANSACTION", report_node="node136"),
    sql("按文档回滚 node136 第一次更新预备事务", JOINER_PORT, "ROLLBACK PREPARED 'stream_c'", "返回 ROLLBACK PREPARED", "ROLLBACK PREPARED", report_node="node136"),
    wait_for_rows("确认 rollback 后 skip 历史不增加", PEER_PORT,
        "SELECT count(*) FROM fdd.mmr_conflict_history WHERE relname='%s' AND conflict_type='multiple_unique_conflicts' AND conflict_resolution='skip'" % TABLE, "1", "node135"),
    sql("按文档在 node136 准备第二次 100 行更新事务", JOINER_PORT,
        "BEGIN; UPDATE %s SET name=repeat('c',1024); PREPARE TRANSACTION 'stream_c'" % TABLE,
        "返回 BEGIN、UPDATE 100、PREPARE TRANSACTION", "BEGIN", "UPDATE 100", "PREPARE TRANSACTION", report_node="node136"),
    sql("按文档提交 node136 第二次更新预备事务", JOINER_PORT, "COMMIT PREPARED 'stream_c'", "返回 COMMIT PREPARED", "COMMIT PREPARED", report_node="node136"),
    wait_for_rows("确认 node134 累计两条 skip 历史", SOURCE_PORT,
        "SELECT count(*) FROM fdd.mmr_conflict_history WHERE relname='%s' AND conflict_type='multiple_unique_conflicts' AND conflict_resolution='skip'" % TABLE, "2", "node134"),
    wait_for_rows("确认 node135 累计两条 skip 历史", PEER_PORT,
        "SELECT count(*) FROM fdd.mmr_conflict_history WHERE relname='%s' AND conflict_type='multiple_unique_conflicts' AND conflict_resolution='skip'" % TABLE, "2", "node135"),
]
