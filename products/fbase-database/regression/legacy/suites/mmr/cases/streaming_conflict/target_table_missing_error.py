"""Streaming conflict document 2.9.2: target_table_missing with error."""

from copy import deepcopy

from framework.assertions import output_contains_text
from suites.mmr.cases.streaming_conflict.delete_missing_skip import PORT135, wait_for_rows
from suites.mmr.cases.streaming_conflict.target_table_missing_skip_if_recently_dropped import (
    CASE as SKIP_DROPPED_CASE, TABLE,
)


CASE = deepcopy(SKIP_DROPPED_CASE)
CASE.update({
    "id": "mmr.streaming_conflict.target_table_missing_error",
    "name": "streaming target_table_missing 的 error 策略", "section": "2.9.2",
    "conflict_configuration": [
        "发布端 node134：debug_logical_replication_streaming=immediate，logical_decoding_work_mem=64kB。",
        "订阅端 node135：debug_logical_replication_streaming=buffered，logical_decoding_work_mem=64kB。",
        "两端 MMR streaming=parallel；node134 two_phase=false，node135 two_phase=true。",
        "所有节点订阅默认复制集 g1；node135 删除本地表后 target_table_missing=error。",
    ],
})
for step in CASE["steps"]:
    if step["title"] == "将 node135 target_table_missing 设为 skip_if_recently_dropped":
        step.update({"title": "将 node135 target_table_missing 设为 error",
                     "display_sql": step["display_sql"].replace("skip_if_recently_dropped", "error"),
                     "argv": step["argv"][:-1] + [step["argv"][-1].replace("skip_if_recently_dropped", "error")],
                     "expected": "返回 node135,target_table_missing,error",
                     "assertion": output_contains_text("node135,target_table_missing,error")})
    elif step["title"] == "确认触发前 skip_if_recently_dropped 冲突记录为 0":
        step.update({"title": "确认触发前 error 冲突记录为 0",
                     "display_sql": step["display_sql"].replace("skip_if_recently_dropped", "error"),
                     "argv": step["argv"][:-1] + [step["argv"][-1].replace("skip_if_recently_dropped", "error")],
                     "assertion": output_contains_text("0")})
    elif step["title"] == "按文档在 node134 插入 512 行事务":
        step.update({"title": "按文档在 node134 插入 error 场景 512 行事务",
                     "display_sql": step["display_sql"].replace("100001,100512", "99001,99512"),
                     "argv": step["argv"][:-1] + [step["argv"][-1].replace("100001,100512", "99001,99512")]})
    elif step["title"] == "确认 node135 记录 512 条 skip_if_recently_dropped 历史":
        CASE["steps"][CASE["steps"].index(step)] = wait_for_rows(
            "确认 node135 记录首条 error 冲突", PORT135,
            "SELECT count(*) FROM fdd.mmr_conflict_history WHERE relname='%s' AND conflict_type='target_table_missing' AND conflict_resolution='error'" % TABLE,
            "1", "node135")
