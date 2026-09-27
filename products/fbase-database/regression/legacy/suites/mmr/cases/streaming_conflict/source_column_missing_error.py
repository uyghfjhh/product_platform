"""Streaming conflict document 2.8.2: source_column_missing with error."""

from copy import deepcopy

from framework.assertions import output_contains_text
from suites.mmr.cases.streaming_conflict.delete_missing_skip import PORT135, wait_for_rows
from suites.mmr.cases.streaming_conflict.source_column_missing_use_default_value import (
    CASE as DEFAULT_CASE, TABLE,
)


CASE = deepcopy(DEFAULT_CASE)
CASE.update({
    "id": "mmr.streaming_conflict.source_column_missing_error",
    "name": "streaming source_column_missing 的 error 策略",
    "section": "2.8.2",
    "conflict_configuration": [
        "发布端 node134：debug_logical_replication_streaming=immediate，logical_decoding_work_mem=64kB。",
        "订阅端 node135：debug_logical_replication_streaming=buffered，logical_decoding_work_mem=64kB。",
        "两端 MMR streaming=parallel；node134 two_phase=false，node135 two_phase=true。",
        "node134 缺少 city，node135 的 city 有默认值；node135 的 source_column_missing=error。",
    ],
})
CASE.pop("session", None)
CASE["fixtures"] = [
    "cluster",
    {"type": "isolated_mmr_node_creation",
     "data_dir": "/tmp/fbase_regress_mmr_stream_delete_missing_{run_id}"},
]
for step in CASE["steps"]:
    if step["title"] == "将 node135 source_column_missing 设为 use_default_value":
        step.update({"title": "将 node135 source_column_missing 设为 error",
                     "display_sql": step["display_sql"].replace("use_default_value", "error"),
                     "argv": step["argv"][:-1] + [step["argv"][-1].replace("use_default_value", "error")],
                     "expected": "返回 node135,source_column_missing,error",
                     "assertion": output_contains_text("node135,source_column_missing,error")})
    elif step["title"] == "确认触发前 use_default_value 冲突记录为 0":
        step.update({"title": "确认触发前 error 冲突记录为 0",
                     "display_sql": step["display_sql"].replace("use_default_value", "error"),
                     "argv": step["argv"][:-1] + [step["argv"][-1].replace("use_default_value", "error")],
                     "assertion": output_contains_text("0")})
    elif step["title"] == "按文档插入缺少 city 的 512 行事务":
        step.update({"title": "按文档插入 error 场景缺少 city 的 512 行事务",
                     "display_sql": step["display_sql"].replace("100001,100512", "99001,99512"),
                     "argv": step["argv"][:-1] + [step["argv"][-1].replace("100001,100512", "99001,99512")]})
    elif step["title"] == "确认 node135 使用默认 city 写入 512 行并记录冲突":
        CASE["steps"][CASE["steps"].index(step)] = wait_for_rows(
            "确认 node135 未写入数据且记录首条 error 冲突", PORT135,
            "SELECT (SELECT count(*) FROM %s WHERE id BETWEEN 99001 AND 99512)::text || '|' || (SELECT count(*) FROM fdd.mmr_conflict_history WHERE relname='%s' AND conflict_type='source_column_missing' AND conflict_resolution='error')::text" %
            (TABLE, TABLE), "0|1", "node135")
