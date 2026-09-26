"""Streaming conflict document 3.10.1: 2PC multiple_unique_conflicts/error."""

from copy import deepcopy

from suites.mmr.cases.streaming_conflict.delete_missing_skip import sql, wait_for_rows
from suites.mmr.cases.streaming_conflict.multiple_unique_conflicts_error import (
    CASE as BASE_CASE, JOINER_PORT, PEER_PORT, SOURCE_PORT, TABLE,
)
from suites.mmr.cases.node_management.create_group import create_node


CASE = deepcopy(BASE_CASE)
CASE.update({
    "id": "mmr.streaming_conflict.two_phase_multiple_unique_conflicts_error",
    "name": "2PC streaming multiple_unique_conflicts 的 error 策略",
    "section": "3.10.1",
})
for index, step in enumerate(CASE["steps"]):
    if step["title"] == "初始化 schema-only node136":
        CASE["steps"][index + 1] = create_node(JOINER_PORT, "node136", "parallel", True)
        CASE["steps"][index + 1]["report"] = False
    if step["title"] == "初始化 node135":
        CASE["steps"][index + 1] = create_node(PEER_PORT, "node135", "parallel", True)
        CASE["steps"][index + 1]["report"] = False
CASE["steps"] = [step for step in CASE["steps"] if "100 行双唯一键冲突事务" not in step["title"] and "确认 node13" not in step["title"]]
CASE["steps"] += [
    sql("按文档在 node136 准备第一次 100 行双唯一键冲突事务", JOINER_PORT,
        "BEGIN; INSERT INTO %s(id,name,age,city,country) SELECT gs,repeat('b',1024),gs,gs,gs FROM generate_series(1,100) gs; PREPARE TRANSACTION 'stream_100'" % TABLE,
        "返回 BEGIN、INSERT 0 100、PREPARE TRANSACTION", "BEGIN", "INSERT 0 100", "PREPARE TRANSACTION", report_node="node136"),
    sql("按文档回滚 node136 第一次预备事务", JOINER_PORT, "ROLLBACK PREPARED 'stream_100'", "返回 ROLLBACK PREPARED", "ROLLBACK PREPARED", report_node="node136"),
    wait_for_rows("确认 rollback 后 node134 无 error、node135 有一条 streaming GID error", PEER_PORT,
        "SELECT (SELECT count(*) FROM fdd.mmr_conflict_history WHERE relname='%s' AND conflict_type='multiple_unique_conflicts' AND conflict_resolution LIKE 'error%%')::text" % TABLE, "1", "node135"),
    sql("按文档在 node136 准备第二次 100 行双唯一键冲突事务", JOINER_PORT,
        "BEGIN; INSERT INTO %s(id,name,age,city,country) SELECT gs,repeat('b',1024),gs,gs,gs FROM generate_series(1,100) gs; PREPARE TRANSACTION 'stream_100'" % TABLE,
        "返回 BEGIN、INSERT 0 100、PREPARE TRANSACTION", "BEGIN", "INSERT 0 100", "PREPARE TRANSACTION", report_node="node136"),
    sql("按文档提交 node136 第二次预备事务", JOINER_PORT, "COMMIT PREPARED 'stream_100'", "返回 COMMIT PREPARED", "COMMIT PREPARED", report_node="node136"),
    wait_for_rows("确认 commit 后 node134 有一条 error", SOURCE_PORT,
        "SELECT count(*) FROM fdd.mmr_conflict_history WHERE relname='%s' AND conflict_type='multiple_unique_conflicts' AND conflict_resolution LIKE 'error%%'" % TABLE, "1", "node134"),
    wait_for_rows("确认 commit 后 node135 累计两条 streaming GID error", PEER_PORT,
        "SELECT count(*) FROM fdd.mmr_conflict_history WHERE relname='%s' AND conflict_type='multiple_unique_conflicts' AND conflict_resolution LIKE 'error%%'" % TABLE, "2", "node135"),
]
