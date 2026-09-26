"""Streaming conflict document 2.11.2: update_origin_change/error."""

from copy import deepcopy

from framework.assertions import output_contains_text
from suites.mmr.cases.streaming_conflict.delete_missing_skip import (
    PORT135, wait_for_rows,
)
from suites.mmr.cases.streaming_conflict.update_origin_change_update_if_newer import (
    CASE as BASE_CASE, PEER_PORT, TABLE,
)


CASE = deepcopy(BASE_CASE)
CASE.update({"id": "mmr.streaming_conflict.update_origin_change_error",
             "name": "streaming update_origin_change 的 error 策略", "section": "2.11.2"})
# All resolver variants run against the same session topology.  Removing this
# session leaves the inherited reset steps pointing at nonexistent node ports.
CASE["session"]["order"] = 260
CASE["conflict_configuration"][-1] = "node135 的 update_origin_change=error；回放应记录首条冲突后停止。"
for index, step in enumerate(CASE["steps"]):
    title = step["title"]
    if title == "将 node135 update_origin_change 设为 update_if_newer":
        step["title"] = "将 node135 update_origin_change 设为 error"
        step["display_sql"] = step["display_sql"].replace("update_if_newer", "error")
        step["argv"][-1] = step["argv"][-1].replace("update_if_newer", "error")
        step["expected"] = "返回 node135,update_origin_change,error"
        step["assertion"] = output_contains_text("node135,update_origin_change,error")
    elif title == "确认触发前 update_if_newer 历史为 0":
        step["title"] = "确认触发前 error 历史为 0"
        step["display_sql"] = step["display_sql"].replace("update_if_newer", "error")
        step["argv"][-1] = step["argv"][-1].replace("update_if_newer", "error")
        step["assertion"] = output_contains_text("0")
    elif title == "按文档在 node134 插入 512 行前置数据":
        step["title"] = "按文档在 node134 插入 error 前置数据"
        step["display_sql"] = step["display_sql"].replace("20001,20512", "19001,19512")
        step["argv"][-1] = step["argv"][-1].replace("20001,20512", "19001,19512")
    elif title in {"等待 node135 收到 512 行", "等待 node136 收到 512 行"}:
        step["display_sql"] = step["display_sql"].replace("20001 AND 20512", "19001 AND 19512")
        step["argv"][-1] = step["argv"][-1].replace("20001 AND 20512", "19001 AND 19512")
    elif title == "按 mmr-autotest 时序执行 node134/node136 的 512 行更新":
        step["display_sql"] = step["display_sql"].replace("20001 AND 20512", "19001 AND 19512")
        step["argv"][-1] = step["argv"][-1].replace("20001 AND 20512", "19001 AND 19512")
    elif title == "确认 node135 记录 512 条 update_if_newer 冲突":
        CASE["steps"][index] = wait_for_rows(
            "确认 node135 记录首条 error 冲突", PEER_PORT,
            "SELECT count(*) FROM fdd.mmr_conflict_history WHERE relname='%s' AND conflict_type='update_origin_change' AND conflict_resolution='error'" % TABLE,
            "1", "node135")
