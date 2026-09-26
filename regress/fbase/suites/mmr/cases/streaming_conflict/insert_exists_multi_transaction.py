"""Supplemental mmr-autotest regression: concurrent insert_exists transactions."""

from framework.assertions import command_succeeds
from framework.steps import command_step
from suites.mmr.cases.node_management.create_group import PSQL
from suites.mmr.cases.streaming_conflict.delete_missing_skip import (
    PORT134, PORT135, sql, wait_for_rows,
)
from suites.mmr.cases.streaming_conflict.update_missing_insert_or_skip import (
    common_streaming_set_steps,
)
from suites.mmr.streaming_conflict_support import non2pc_case_base


def concurrent_conflicting_inserts():
    script = (
        "log_dir=/tmp/fbase_regress_insert_exists_multi; rm -rf \"$log_dir\"; mkdir -p \"$log_dir\"; "
        "status=0; for i in $(seq 1 10); do "
        "%s -X -v ON_ERROR_STOP=1 -P pager=off -h 127.0.0.1 -p %s -U postgres -d postgres "
        "-c \"BEGIN; INSERT INTO immediate_parallel_conflict VALUES($i, 'remote-' || $i); SELECT pg_sleep(3); COMMIT;\" "
        ">\"$log_dir/$i.log\" 2>&1 & done; "
        "for pid in $(jobs -p); do wait \"$pid\" || status=1; done; cat \"$log_dir\"/*.log; "
        "rm -rf \"$log_dir\"; exit \"$status\"" % (PSQL, PORT135))
    step = command_step(
        "按 mmr-autotest 并发提交 10 个 insert_exists 冲突事务",
        ["sh", "-ec", script],
        "10 个独立事务均执行 BEGIN、INSERT、pg_sleep(3)、COMMIT 并成功结束",
        command_succeeds(), node="mmr:mmr1", timeout=30,
        display_sql=(
            "-- node135，10 个独立会话并发执行，i=1..10\n"
            "BEGIN; INSERT INTO immediate_parallel_conflict VALUES(i, 'remote-' || i); "
            "SELECT pg_sleep(3); COMMIT;"),
        report_node="node135")
    step["report_database"] = "postgres"
    return step


CASE = non2pc_case_base(
    "mmr.streaming_conflict.insert_exists_multi_transaction",
    "streaming insert_exists 十并发事务的 skip 策略", "2.3.3",
    [
        "发布端 node135：debug_logical_replication_streaming=buffered，logical_decoding_work_mem=64kB。",
        "订阅端 node134：debug_logical_replication_streaming=immediate，logical_decoding_work_mem=64kB。",
        "两端 MMR streaming=parallel；node134 two_phase=false，node135 two_phase=true。",
        "参考 mmr-autotest：10 个独立事务并发写入目标端已存在的 id=1..10，全部提交后逐条进入 skip。",
    ])

CASE["fixtures"] = [
    "cluster",
    {"type": "isolated_mmr_node_creation",
     "data_dir": "/tmp/fbase_regress_mmr_stream_insert_multi_{run_id}"},
]
CASE["steps"] = common_streaming_set_steps() + [
    sql("将 node134 insert_exists 设为 skip", PORT134,
        "SELECT fdd.alter_local_node_set_conflict_resolver('insert_exists','skip')",
        "返回 node134,insert_exists,skip", "node134,insert_exists,skip",
        report_node="node134"),
    sql("确认触发前 node134 没有 insert_exists 历史", PORT134,
        "SELECT count(*) FROM fdd.mmr_conflict_history WHERE relname='immediate_parallel_conflict' AND conflict_type='insert_exists'",
        "返回 0", "0", report_node="node134"),
    concurrent_conflicting_inserts(),
    wait_for_rows("确认 10 个并发冲突均被 skip 且 node134 保留原值", PORT134,
                  "SELECT (SELECT count(*) FROM immediate_parallel_conflict WHERE id BETWEEN 1 AND 10 AND name='a')::text || '|' || (SELECT count(*) FROM fdd.mmr_conflict_history WHERE relname='immediate_parallel_conflict' AND conflict_type='insert_exists' AND conflict_resolution='skip')::text",
                  "10|10", "node134"),
]
