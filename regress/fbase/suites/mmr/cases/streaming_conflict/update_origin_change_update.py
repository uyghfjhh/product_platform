"""Streaming conflict document 2.11.4: update_origin_change/update."""

from copy import deepcopy

from framework.assertions import output_contains_text
from suites.mmr.cases.streaming_conflict.update_origin_change_update_if_newer import CASE as BASE_CASE


CASE = deepcopy(BASE_CASE)
CASE.update({"id": "mmr.streaming_conflict.update_origin_change_update",
             "name": "streaming update_origin_change 的 update 策略", "section": "2.11.4"})
CASE["session"]["order"] = 280
CASE["conflict_configuration"][-1] = "node135 的 update_origin_change=update。"
for step in CASE["steps"]:
    if step.get("session_reset"):
        continue
    display_sql = step.get("display_sql") or ""
    if "update_if_newer" in display_sql:
        step["display_sql"] = step["display_sql"].replace("update_if_newer", "update")
        step["argv"][-1] = step["argv"][-1].replace("update_if_newer", "update")
    if step["title"] == "将 node135 update_origin_change 设为 update_if_newer":
        step["title"] = "将 node135 update_origin_change 设为 update"
        step["expected"] = "返回 node135,update_origin_change,update"
        step["assertion"] = output_contains_text("node135,update_origin_change,update")
    if "20001" in display_sql:
        step["display_sql"] = step["display_sql"].replace("20001", "17001").replace("20512", "17512")
        step["argv"][-1] = step["argv"][-1].replace("20001", "17001").replace("20512", "17512")
