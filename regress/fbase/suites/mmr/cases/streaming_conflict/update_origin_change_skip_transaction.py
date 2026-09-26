"""Streaming conflict document 2.11.5: update_origin_change/skip_transaction."""

from copy import deepcopy

from framework.assertions import output_contains_text
from suites.mmr.cases.streaming_conflict.delete_missing_skip import wait_for_rows
from suites.mmr.cases.streaming_conflict.update_origin_change_update_if_newer import (
    CASE as BASE_CASE, PEER_PORT, TABLE,
)


CASE = deepcopy(BASE_CASE)
CASE.update({"id": "mmr.streaming_conflict.update_origin_change_skip_transaction",
             "name": "streaming update_origin_change 的 skip_transaction 策略", "section": "2.11.5"})
CASE["session"]["order"] = 290
CASE["conflict_configuration"][-1] = "node135 的 update_origin_change=skip_transaction；首条冲突跳过远端事务。"
for index, step in enumerate(CASE["steps"]):
    if step.get("session_reset"):
        continue
    display_sql = step.get("display_sql") or ""
    if "update_if_newer" in display_sql:
        step["display_sql"] = step["display_sql"].replace("update_if_newer", "skip_transaction")
        step["argv"][-1] = step["argv"][-1].replace("update_if_newer", "skip_transaction")
    if step["title"] == "将 node135 update_origin_change 设为 update_if_newer":
        step["title"] = "将 node135 update_origin_change 设为 skip_transaction"
        step["expected"] = "返回 node135,update_origin_change,skip_transaction"
        step["assertion"] = output_contains_text("node135,update_origin_change,skip_transaction")
    if "20001" in display_sql:
        step["display_sql"] = step["display_sql"].replace("20001", "16001").replace("20512", "16512")
        step["argv"][-1] = step["argv"][-1].replace("20001", "16001").replace("20512", "16512")
    if step["title"] == "确认 node135 记录 512 条 update_if_newer 冲突":
        CASE["steps"][index] = wait_for_rows(
            "确认 node135 记录首条 skip_transaction 冲突", PEER_PORT,
            "SELECT count(*) FROM fdd.mmr_conflict_history WHERE relname='%s' AND conflict_type='update_origin_change' AND conflict_resolution='skip_transaction'" % TABLE,
            "1", "node135")
