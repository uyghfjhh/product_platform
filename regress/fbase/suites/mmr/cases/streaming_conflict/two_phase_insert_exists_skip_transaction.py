"""Streaming conflict document 3.3.5: 2PC insert_exists/skip_transaction."""

from suites.mmr.two_phase_conflict_support import (PORT135,
                                                    two_phase_insert_exists_case,
                                                    wait_for_rows)


CASE = two_phase_insert_exists_case(
    "mmr.streaming_conflict.two_phase_insert_exists_skip_transaction",
    "2PC streaming insert_exists 的 skip_transaction 策略", "3.3.5",
    "skip_transaction", 96, 96001, "b", "stream_96", "1", "2", False,
    "skip_transaction%", commit_range_rows=0)


def _hidden_prepared_conflict_wait(title, expected):
    step = wait_for_rows(
        title, PORT135,
        "SELECT count(*) FROM fdd.mmr_conflict_history WHERE "
        "relname='immediate_parallel_insert_exists' AND conflict_type='insert_exists' "
        "AND conflict_resolution LIKE 'skip_transaction%%'", expected, "node135", timeout=15)
    step["report"] = False
    return step


for index in range(len(CASE["steps"]) - 1, -1, -1):
    title = CASE["steps"][index]["title"]
    if title == "按文档回滚第一次预备事务":
        CASE["steps"].insert(index, _hidden_prepared_conflict_wait(
            "等待第一次 PREPARE 在 node135 完成 skip_transaction 冲突预处理", "1"))
    elif title == "按文档提交第二次预备事务":
        CASE["steps"].insert(index, _hidden_prepared_conflict_wait(
            "等待第二次 PREPARE 在 node135 完成 skip_transaction 冲突预处理", "2"))
