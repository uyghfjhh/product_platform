"""Streaming conflict document 2.11.3: update_origin_change/skip."""

from copy import deepcopy

from suites.mmr.cases.streaming_conflict.delete_missing_skip import sql, wait_for_rows
from suites.mmr.cases.streaming_conflict.update_origin_change_error import CASE as ERROR_CASE
from suites.mmr.cases.streaming_conflict.update_origin_change_update_if_newer import (
    OBSERVER_PORT, PEER_PORT, SOURCE_PORT, TABLE,
    ordered_origin_change,
)


CASE = deepcopy(ERROR_CASE)
CASE.update({"id": "mmr.streaming_conflict.update_origin_change_skip",
             "name": "streaming update_origin_change 的 skip 策略", "section": "2.11.3"})
CASE["session"]["order"] = 270
CASE["conflict_configuration"][-1] = "node135 的 update_origin_change=skip；先按 2.11.2 重建 error 回放前态。"
CASE["steps"] += [
    sql("将 node135 update_origin_change 改为 skip", PEER_PORT,
        "SELECT fdd.alter_local_node_set_conflict_resolver('update_origin_change','skip')",
        "返回 node135,update_origin_change,skip", "node135,update_origin_change,skip", report_node="node135"),
    wait_for_rows("确认 worker 重试后已有 512 条 skip 历史", PEER_PORT,
        "SELECT count(*) FROM fdd.mmr_conflict_history WHERE relname='%s' AND conflict_type='update_origin_change' AND conflict_resolution='skip'" % TABLE,
        "512", "node135"),
    sql("按文档在 node134 插入第二批 512 行前置数据", SOURCE_PORT,
        "INSERT INTO %s(id,name) SELECT gs,repeat('a',128) FROM generate_series(18001,18512) gs" % TABLE,
        "返回 INSERT 0 512", "INSERT 0 512", report_node="node134"),
    wait_for_rows("等待 node135 收到第二批 512 行", PEER_PORT,
        "SELECT count(*) FROM %s WHERE id BETWEEN 18001 AND 18512" % TABLE, "512", "node135"),
    wait_for_rows("等待 node136 收到第二批 512 行", OBSERVER_PORT,
        "SELECT count(*) FROM %s WHERE id BETWEEN 18001 AND 18512" % TABLE, "512", "node136"),
    ordered_origin_change("按 mmr-autotest 时序执行第二批 node134/node136 更新", 18001, 18512),
    wait_for_rows("确认 node135 累计 1024 条 skip 历史", PEER_PORT,
        "SELECT count(*) FROM fdd.mmr_conflict_history WHERE relname='%s' AND conflict_type='update_origin_change' AND conflict_resolution='skip'" % TABLE,
        "1024", "node135"),
]
