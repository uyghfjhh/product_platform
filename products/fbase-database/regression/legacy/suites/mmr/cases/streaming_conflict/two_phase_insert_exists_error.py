"""Streaming conflict document 3.3.2: 2PC insert_exists/error."""

from suites.mmr.two_phase_conflict_support import (PORT135, sql,
                                                    two_phase_insert_exists_case,
                                                    wait_for_rows)


CASE = two_phase_insert_exists_case(
    "mmr.streaming_conflict.two_phase_insert_exists_error",
    "2PC streaming insert_exists 的 error 策略", "3.3.2",
    "error", 99, 99001, "b", "stream_99", "1", "2", False, "error%",
    commit_range_rows=0)


HISTORY = ("SELECT conflict_resolution, remote_xid::text, remote_commit_lsn::text, count(*) "
           "FROM fdd.mmr_conflict_history WHERE relname='immediate_parallel_insert_exists' "
           "AND conflict_type='insert_exists' AND conflict_resolution LIKE 'error%%' "
           "GROUP BY conflict_resolution, remote_xid, remote_commit_lsn "
           "ORDER BY conflict_resolution, remote_xid, remote_commit_lsn")


def _hidden_prepared_conflict_wait(title, expected):
    step = wait_for_rows(
        title, PORT135,
        "SELECT count(*) FROM fdd.mmr_conflict_history WHERE "
        "relname='immediate_parallel_insert_exists' AND conflict_type='insert_exists' "
        "AND conflict_resolution LIKE 'error%%'", expected, "node135", timeout=15)
    step["report"] = False
    return step


for index in range(len(CASE["steps"]) - 1, -1, -1):
    title = CASE["steps"][index]["title"]
    if title == "按文档回滚第一次预备事务":
        CASE["steps"].insert(index, _hidden_prepared_conflict_wait(
            "等待第一次 PREPARE 在 node135 完成 insert_exists error 冲突预处理", "1"))
    elif title.startswith("确认 rollback 后"):
        CASE["steps"].insert(index + 1, sql(
            "展示 rollback 后每条 insert_exists error 历史的 GID、远端 xid 和 LSN", PORT135,
            HISTORY, "显示 error 历史明细", "error", report_node="node135"))
    elif title == "按文档提交第二次预备事务":
        CASE["steps"].insert(index, _hidden_prepared_conflict_wait(
            "等待第二次 PREPARE 在 node135 完成 insert_exists error 冲突预处理", "2"))
