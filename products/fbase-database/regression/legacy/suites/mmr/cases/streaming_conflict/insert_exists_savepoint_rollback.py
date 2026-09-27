"""Supplemental regression from mmr-autotest: rolled-back insert_exists."""

from suites.mmr.cases.streaming_conflict.delete_missing_skip import (
    PORT134, PORT135, sql, wait_for_rows,
)
from suites.mmr.cases.streaming_conflict.update_missing_insert_or_skip import (
    common_streaming_set_steps,
)
from suites.mmr.streaming_conflict_support import non2pc_case_base


CASE = non2pc_case_base(
    "mmr.streaming_conflict.insert_exists_savepoint_rollback",
    "streaming insert_exists 子事务回滚不产生冲突", "2.3.1",
    [
        "发布端 node135：debug_logical_replication_streaming=buffered，logical_decoding_work_mem=64kB。",
        "订阅端 node134：debug_logical_replication_streaming=immediate，logical_decoding_work_mem=64kB。",
        "两端 MMR streaming=parallel；node134 two_phase=false，node135 two_phase=true。",
        "参考 mmr-autotest 的 SAVEPOINT 回滚场景：回滚的 id=80 不得进入远端回放；保留的两行必须正常复制。",
    ])

CASE["fixtures"] = [
    "cluster",
    {"type": "isolated_mmr_node_creation",
     "data_dir": "/tmp/fbase_regress_mmr_stream_savepoint_{run_id}"},
]
CASE["steps"] = common_streaming_set_steps() + [
    sql("将 node134 insert_exists 设为 error 以放大意外回放", PORT134,
        "SELECT fdd.alter_local_node_set_conflict_resolver('insert_exists','error')",
        "返回 node134,insert_exists,error", "node134,insert_exists,error",
        report_node="node134"),
    sql("确认触发前 node134 没有 insert_exists 历史", PORT134,
        "SELECT count(*) FROM fdd.mmr_conflict_history WHERE relname='immediate_parallel_conflict' AND conflict_type='insert_exists'",
        "返回 0", "0", report_node="node134"),
    sql("按 mmr-autotest 在 node135 执行包含 SAVEPOINT 回滚的事务", PORT135,
        "BEGIN; INSERT INTO immediate_parallel_conflict VALUES(200,'kept-before'); "
        "SAVEPOINT insert_exists_sp; INSERT INTO immediate_parallel_conflict VALUES(80,'discarded-conflict'); "
        "ROLLBACK TO SAVEPOINT insert_exists_sp; INSERT INTO immediate_parallel_conflict VALUES(201,'kept-after'); COMMIT",
        "返回 BEGIN、两个保留 INSERT、SAVEPOINT、ROLLBACK、COMMIT",
        "BEGIN", "INSERT 0 1", "SAVEPOINT", "ROLLBACK", "COMMIT", report_node="node135"),
    wait_for_rows("确认 node134 仅收到两条保留行且未记录 insert_exists 冲突", PORT134,
                  "SELECT (SELECT count(*) FROM immediate_parallel_conflict WHERE id IN (200,201) AND name IN ('kept-before','kept-after'))::text || '|' || (SELECT count(*) FROM immediate_parallel_conflict WHERE id=80 AND name='discarded-conflict')::text || '|' || (SELECT count(*) FROM fdd.mmr_conflict_history WHERE relname='immediate_parallel_conflict' AND conflict_type='insert_exists')::text",
                  "2|0|0", "node134"),
]
