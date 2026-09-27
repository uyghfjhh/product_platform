"""Streaming conflict document 2.10.1: multiple_unique_conflicts error."""

from copy import deepcopy

from suites.mmr.cases.conflict.multiple_unique_conflicts import (
    CASE as BASE_CASE, JOINER_PORT, PEER_PORT, SOURCE_PORT, TABLE,
)
from suites.mmr.cases.streaming_conflict.delete_missing_skip import sql, wait_for_rows


CASE = deepcopy(BASE_CASE)
CASE.update({
    "id": "mmr.streaming_conflict.multiple_unique_conflicts_error",
    "name": "streaming multiple_unique_conflicts 的 error 策略",
    "document": "多活streaming冲突处理测试文档.md", "section": "2.10.1",
    "group": "streaming_conflict",
    "conflict_configuration": [
        "node134/node135 为对等 MMR 成员；node136 以 schema-only 加入。",
        "node134/node135 的 multiple_unique_conflicts=error。",
    ],
})

# Reuse only the three-node lifecycle.  The document-specific data and DML
# below replace the functional-document scenario's one-row conflict.
for step in CASE["steps"]:
    command = " ".join(step.get("argv") or [])
    if "INSERT INTO public.%s VALUES(1,'name',1,1,1),(2,'name',2,2,2)" % TABLE in command:
        step["argv"][-1] = step["argv"][-1].replace(
            "VALUES(1,'name',1,1,1),(2,'name',2,2,2)",
            "VALUES(102,repeat('a',64),4,4,4),(4,repeat('a',64),103,103,103)")
    if "INSERT INTO public.%s VALUES(4,'name',1,2,2)" % TABLE in command:
        step["report"] = False

CASE["steps"] = [step for step in CASE["steps"] if step["title"] not in {
    "确认 node134 触发前没有旧的 multiple_unique_conflicts",
    "按文档在 node136 插入同时冲突 age 和 city/country 的数据",
    "等待 node134 记录 multiple_unique_conflicts",
    "展示 node134 的多唯一约束冲突记录",
    "确认 node134 数据未因冲突改变",
    "确认 node135 数据未因冲突改变",
}]

CASE["steps"] += [
    sql("将 node134 multiple_unique_conflicts 设为 error", SOURCE_PORT,
        "SELECT fdd.alter_local_node_set_conflict_resolver('multiple_unique_conflicts','error')",
        "返回 node134,multiple_unique_conflicts,error", "node134,multiple_unique_conflicts,error", report_node="node134"),
    sql("将 node135 multiple_unique_conflicts 设为 error", PEER_PORT,
        "SELECT fdd.alter_local_node_set_conflict_resolver('multiple_unique_conflicts','error')",
        "返回 node135,multiple_unique_conflicts,error", "node135,multiple_unique_conflicts,error", report_node="node135"),
    sql("确认 node134 触发前 error 冲突记录为 0", SOURCE_PORT,
        "SELECT count(*) FROM fdd.mmr_conflict_history WHERE relname='%s' AND conflict_type='multiple_unique_conflicts' AND conflict_resolution='error'" % TABLE,
        "返回 0", "0", report_node="node134"),
    sql("确认 node135 触发前 error 冲突记录为 0", PEER_PORT,
        "SELECT count(*) FROM fdd.mmr_conflict_history WHERE relname='%s' AND conflict_type='multiple_unique_conflicts' AND conflict_resolution='error'" % TABLE,
        "返回 0", "0", report_node="node135"),
    sql("按文档在 node136 插入 100 行双唯一键冲突事务", JOINER_PORT,
        "BEGIN; INSERT INTO %s(id,name,age,city,country) SELECT gs,repeat('b',1024),gs,gs,gs FROM generate_series(1,100) gs; COMMIT" % TABLE,
        "返回 BEGIN、INSERT 0 100、COMMIT", "BEGIN", "INSERT 0 100", "COMMIT", report_node="node136"),
    wait_for_rows("确认 node134 保持两行初始数据并记录首条 error 冲突", SOURCE_PORT,
        "SELECT (SELECT count(*) FROM %s)::text || '|' || (SELECT count(*) FROM fdd.mmr_conflict_history WHERE relname='%s' AND conflict_type='multiple_unique_conflicts' AND conflict_resolution='error')::text" % (TABLE,TABLE), "2|1", "node134"),
    wait_for_rows("确认 node135 保持两行初始数据并记录首条 error 冲突", PEER_PORT,
        "SELECT (SELECT count(*) FROM %s)::text || '|' || (SELECT count(*) FROM fdd.mmr_conflict_history WHERE relname='%s' AND conflict_type='multiple_unique_conflicts' AND conflict_resolution='error')::text" % (TABLE,TABLE), "2|1", "node135"),
]
