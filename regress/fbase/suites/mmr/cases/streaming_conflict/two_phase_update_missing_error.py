"""Streaming conflict document 3.2.2: 2PC update_missing/error."""

from suites.mmr.two_phase_conflict_support import (PORT135, TABLE,
                                                    two_phase_update_missing_case,
                                                    wait_for_rows)


CASE = two_phase_update_missing_case(
    "mmr.streaming_conflict.two_phase_update_missing_error",
    "2PC streaming update_missing 的 error 策略", "3.2.2",
    "error", 89, "c", "stream_89", "1", "2", False, "error%")


def _hidden_prepared_conflict_wait(title, expected):
    step = wait_for_rows(
        title, PORT135,
        "SELECT count(*) FROM fdd.mmr_conflict_history WHERE relname='%s' "
        "AND conflict_type='update_missing' AND conflict_resolution LIKE 'error%%'" % TABLE,
        expected, "node135", timeout=15)
    step["report"] = False
    return step


for index in range(len(CASE["steps"]) - 1, -1, -1):
    title = CASE["steps"][index]["title"]
    if title == "按文档回滚第一次预备事务":
        CASE["steps"].insert(index, _hidden_prepared_conflict_wait(
            "等待第一次 PREPARE 在 node135 完成 error 冲突预处理", "1"))
    elif title == "按文档提交第二次预备事务":
        CASE["steps"].insert(index, _hidden_prepared_conflict_wait(
            "等待第二次 PREPARE 在 node135 完成 error 冲突预处理", "2"))
