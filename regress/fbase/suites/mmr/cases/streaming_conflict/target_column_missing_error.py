"""Streaming conflict document 2.7.3: target_column_missing with error."""

from copy import deepcopy

from framework.assertions import output_contains_text
from suites.mmr.cases.streaming_conflict.target_column_missing_ignore_if_null import (
    CASE as IGNORE_IF_NULL_CASE, TABLE,
)


CASE = deepcopy(IGNORE_IF_NULL_CASE)
CASE.update({
    "id": "mmr.streaming_conflict.target_column_missing_error",
    "name": "streaming target_column_missing 的 error 策略",
    "section": "2.7.3",
    "conflict_configuration": [
        "发布端 node134：debug_logical_replication_streaming=immediate，logical_decoding_work_mem=64kB。",
        "订阅端 node135：debug_logical_replication_streaming=buffered，logical_decoding_work_mem=64kB。",
        "两端 MMR streaming=parallel；node134 two_phase=false，node135 two_phase=true。",
        "node134 表含 city 默认列，node135 缺少 city；node135 的 target_column_missing=error。",
    ],
})
CASE.pop("session", None)
CASE["fixtures"] = [
    "cluster",
    {"type": "isolated_mmr_node_creation",
     "data_dir": "/tmp/fbase_regress_mmr_stream_delete_missing_{run_id}"},
]
for step in CASE["steps"]:
    if step["title"] == "将 node135 target_column_missing 设为 ignore_if_null":
        step.update({
            "title": "将 node135 target_column_missing 设为 error",
            "display_sql": "SELECT fdd.alter_local_node_set_conflict_resolver('target_column_missing','error')",
            "argv": step["argv"][:-1] + [step["argv"][-1].replace("ignore_if_null", "error")],
            "expected": "返回 node135,target_column_missing,error",
            "assertion": output_contains_text("node135,target_column_missing,error"),
        })
    elif step["title"] == "确认触发前 ignore_if_null 冲突记录为 0":
        step.update({
            "title": "确认触发前 error 冲突记录为 0",
            "display_sql": step["display_sql"].replace("ignore_if_null", "error"),
            "argv": step["argv"][:-1] + [step["argv"][-1].replace("ignore_if_null", "error")],
            "assertion": output_contains_text("0"),
        })
    elif step["title"] == "按文档插入带非 NULL city 的 512 行事务":
        step.update({
            "title": "按文档插入 error 场景带 city 的 512 行事务",
            "display_sql": step["display_sql"].replace("100001,100512", "98001,98512"),
            "argv": step["argv"][:-1] + [step["argv"][-1].replace("100001,100512", "98001,98512")],
        })
    elif step["title"] == "确认 node135 未插入数据且记录首条 ignore_if_null 冲突":
        step.update({
            "title": "确认 node135 未插入数据且记录首条 error 冲突",
            "display_sql": step["display_sql"].replace("100001 AND 100512", "98001 AND 98512").replace("ignore_if_null", "error"),
            "argv": step["argv"][:-1] + [step["argv"][-1].replace("100001 AND 100512", "98001 AND 98512").replace("ignore_if_null", "error")],
        })
