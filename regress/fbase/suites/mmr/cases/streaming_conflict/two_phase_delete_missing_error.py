"""Streaming conflict document 3.1.2: 2PC delete_missing/error."""

from copy import deepcopy

from framework.assertions import output_contains_text
from suites.mmr.cases.streaming_conflict.delete_missing_skip import wait_for_rows
from suites.mmr.cases.streaming_conflict.two_phase_delete_missing_skip import (
    CASE as SKIP_CASE, PORT134, PORT135, TABLE,
)


CASE = deepcopy(SKIP_CASE)
CASE.update({"id": "mmr.streaming_conflict.two_phase_delete_missing_error",
             "name": "2PC streaming delete_missing 的 error 策略", "section": "3.1.2"})
CASE["conflict_configuration"][-1] = "node135 的 delete_missing=error；rollback/commit 均保留带 GID 的错误历史。"
for index, step in enumerate(CASE["steps"]):
    display_sql = step.get("display_sql") or ""
    if "delete_missing','skip" in display_sql or "conflict_resolution='skip'" in display_sql:
        step["display_sql"] = step["display_sql"].replace("delete_missing','skip", "delete_missing','error").replace("conflict_resolution='skip'", "conflict_resolution LIKE 'error%'")
        step["argv"][-1] = step["argv"][-1].replace("delete_missing','skip", "delete_missing','error").replace("conflict_resolution='skip'", "conflict_resolution LIKE 'error%'")
    if step["title"] == "将 node135 delete_missing 设为 skip":
        step["title"] = "将 node135 delete_missing 设为 error"
        step["expected"] = "返回 node135,delete_missing,error"
        step["assertion"] = output_contains_text("node135,delete_missing,error")
    if "100001" in display_sql:
        step["display_sql"] = step["display_sql"].replace("100001", "99001").replace("100512", "99512").replace("id=100", "id=99").replace("stream_100", "stream_99")
        step["argv"][-1] = step["argv"][-1].replace("100001", "99001").replace("100512", "99512").replace("id=100", "id=99").replace("stream_100", "stream_99")
    if step["title"] == "确认 rollback 后 node134 保留 513 行、node135 保留 512 行且无历史":
        CASE["steps"][index] = wait_for_rows("确认 rollback 后 node135 保留 512 行且记录一条 error 历史", PORT135,
            "SELECT (SELECT count(*) FROM %s WHERE id=99 OR id BETWEEN 99001 AND 99512)::text || '|' || (SELECT count(*) FROM fdd.mmr_conflict_history WHERE relname='%s' AND conflict_type='delete_missing' AND conflict_resolution LIKE 'error%%')::text" % (TABLE,TABLE), "512|1", "node135")
    if step["title"] == "确认 commit 后 node135 删除 512 行并记录一条 skip 历史":
        CASE["steps"][index] = wait_for_rows("确认 commit 后 node135 保留 512 行且累计两条 error 历史", PORT135,
            "SELECT (SELECT count(*) FROM %s WHERE id=99 OR id BETWEEN 99001 AND 99512)::text || '|' || (SELECT count(*) FROM fdd.mmr_conflict_history WHERE relname='%s' AND conflict_type='delete_missing' AND conflict_resolution LIKE 'error%%')::text" % (TABLE,TABLE), "512|2", "node135")

for step in CASE["steps"]:
    if step.get("display_sql"):
        step["display_sql"] = step["display_sql"].replace("stream_100", "stream_99")
    if step.get("argv"):
        step["argv"][-1] = step["argv"][-1].replace("stream_100", "stream_99")


def _hidden_prepared_conflict_wait(title, expected):
    step = wait_for_rows(
        title, PORT135,
        "SELECT count(*) FROM fdd.mmr_conflict_history WHERE relname='%s' "
        "AND conflict_type='delete_missing' AND conflict_resolution LIKE 'error%%'" % TABLE,
        expected, "node135", timeout=15)
    step["report"] = False
    return step


# The apply worker processes a streaming 2PC transaction after PREPARE.  Do not
# resolve the GID until its out-of-band error history is present; otherwise the
# test races the state that the transfer document asks operators to observe.
for index in range(len(CASE["steps"]) - 1, -1, -1):
    title = CASE["steps"][index]["title"]
    if title == "按文档回滚第一次预备事务":
        CASE["steps"].insert(index, _hidden_prepared_conflict_wait(
            "等待第一次 PREPARE 在 node135 完成 error 冲突预处理", "1"))
    elif title == "按文档提交第二次预备事务":
        CASE["steps"].insert(index, _hidden_prepared_conflict_wait(
            "等待第二次 PREPARE 在 node135 完成 error 冲突预处理", "2"))
