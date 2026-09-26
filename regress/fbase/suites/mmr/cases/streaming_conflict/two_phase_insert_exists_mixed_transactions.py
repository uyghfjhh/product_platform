"""Supplemental mmr-autotest regression for mixed 2PC insert_exists flows."""

from framework.assertions import output_contains_text
from framework.steps import command_step
from suites.mmr.cases.node_management.create_group import PSQL
from suites.mmr.cases.streaming_conflict.delete_missing_skip import (
    PORT134, PORT135, sql, wait_for_rows,
)
from suites.mmr.streaming_conflict_support import non2pc_case_base
from suites.mmr.two_phase_conflict_support import two_phase_streaming_set_steps


TABLE = "immediate_parallel_insert_exists_mixed"


def mixed_transactions():
    """Keep the source regression's three-by-four transaction mix intact."""
    script = (
        "work=/tmp/fbase_regress_mixed_insert_exists; rm -rf \"$work\"; mkdir -p \"$work\"; "
        "status=0; "
        "for i in 1 5 9; do "
        "%s -X -v ON_ERROR_STOP=1 -P pager=off -h 127.0.0.1 -p %s -U postgres -d postgres "
        "-c \"BEGIN; INSERT INTO %s VALUES($i, 'prepared-' || $i); PREPARE TRANSACTION 'mixed_2pc_$i';\" "
        ">\"$work/prepared_$i.log\" 2>&1 & done; "
        "for i in 2 6 10; do "
        "%s -X -v ON_ERROR_STOP=1 -P pager=off -h 127.0.0.1 -p %s -U postgres -d postgres "
        "-c \"BEGIN; INSERT INTO %s VALUES($i, 'rollback-' || $i); SELECT pg_sleep(3); ROLLBACK;\" "
        ">\"$work/rollback_$i.log\" 2>&1 & done; "
        "for i in 3 7 11; do "
        "%s -X -v ON_ERROR_STOP=1 -P pager=off -h 127.0.0.1 -p %s -U postgres -d postgres "
        "-c \"BEGIN; INSERT INTO %s VALUES($i, 'plain-' || $i); SELECT pg_sleep(3); COMMIT;\" "
        ">\"$work/plain_$i.log\" 2>&1 & done; "
        "for i in 1000 1001 1002; do "
        "%s -X -v ON_ERROR_STOP=1 -P pager=off -h 127.0.0.1 -p %s -U postgres -d postgres "
        "-c \"INSERT INTO %s VALUES($i, 'normal-' || $i);\" >\"$work/normal_$i.log\" 2>&1 || status=1; done; "
        "for pid in $(jobs -p); do wait \"$pid\" || status=1; done; "
        "for i in 1 5 9; do %s -X -v ON_ERROR_STOP=1 -P pager=off -h 127.0.0.1 -p %s -U postgres -d postgres "
        "-c \"COMMIT PREPARED 'mixed_2pc_$i';\" >>\"$work/prepared_$i.log\" 2>&1 || status=1; done; "
        "cat \"$work\"/*.log; rm -rf \"$work\"; exit \"$status\"" %
        (PSQL, PORT134, TABLE, PSQL, PORT134, TABLE, PSQL, PORT134, TABLE,
         PSQL, PORT134, TABLE, PSQL, PORT134))
    step = command_step(
        "按 mmr-autotest 混合执行 3 个 2PC、3 个回滚、3 个普通冲突事务和 3 条正常数据",
        ["sh", "-ec", script],
        "3 个 2PC 均 PREPARE 后 COMMIT PREPARED；3 个回滚不产生远端冲突；3 个普通事务提交；3 条正常数据同步",
        output_contains_text("PREPARE TRANSACTION", "COMMIT PREPARED", "ROLLBACK", "COMMIT", "INSERT 0 1"),
        node="mmr:mmr1", timeout=50,
        display_sql=(
            "-- node134，i=1,5,9\nBEGIN; INSERT INTO %s VALUES(i, 'prepared-' || i); "
            "PREPARE TRANSACTION 'mixed_2pc_i';\n"
            "-- node134，i=2,6,10\nBEGIN; INSERT INTO %s VALUES(i, 'rollback-' || i); "
            "SELECT pg_sleep(3); ROLLBACK;\n"
            "-- node134，i=3,7,11\nBEGIN; INSERT INTO %s VALUES(i, 'plain-' || i); "
            "SELECT pg_sleep(3); COMMIT;\n"
            "-- node134，i=1000,1001,1002\nINSERT INTO %s VALUES(i, 'normal-' || i);\n"
            "-- node134\nCOMMIT PREPARED 'mixed_2pc_i';" %
            (TABLE, TABLE, TABLE, TABLE)),
        report_node="node134")
    step["report_database"] = "postgres"
    return step


CASE = non2pc_case_base(
    "mmr.streaming_conflict.two_phase_insert_exists_mixed_transactions",
    "2PC、回滚和普通事务混合的 streaming insert_exists", "3.3.3",
    [
        "node134=parallel,two_phase=false；node135=parallel,two_phase=true。",
        "node134=immediate、node135=buffered，logical_decoding_work_mem=64kB。",
        "参考 mmr-autotest：3 个预备提交冲突、3 个预备回滚、3 个普通提交冲突和 3 条无冲突行。",
    ])

CASE["fixtures"] = [
    "cluster",
    {"type": "isolated_mmr_node_creation",
     "data_dir": "/tmp/fbase_regress_mmr_stream_insert_mixed_{run_id}"},
]
CASE["steps"] = two_phase_streaming_set_steps() + [
    sql("创建 node134 的混合事务测试表", PORT134,
        "CREATE TABLE %s(id int PRIMARY KEY,name text)" % TABLE,
        "返回 CREATE TABLE", "CREATE TABLE", report_node="node134"),
    sql("创建 node135 的测试表并插入九个冲突主键", PORT135,
        "CREATE TABLE %s(id int PRIMARY KEY,name text); INSERT INTO %s SELECT i,'a' FROM generate_series(1,11) i WHERE i IN (1,3,5,7,9,11)" % (TABLE, TABLE),
        "返回 CREATE TABLE、INSERT 0 6", "CREATE TABLE", "INSERT 0 6", report_node="node135"),
    sql("在 node135 将混合事务测试表加入 set1", PORT135,
        "SELECT fdd.replication_set_add_table('%s'::regclass,'set1',false,false)" % TABLE,
        "复制集绑定成功", "replication_set_add_table", report_node="node135"),
    sql("在所有节点订阅 set1", PORT134,
        "SELECT fdd.run_on_all_nodes('select fdd.alter_node_replication_sets(''{set1}'');')",
        "node134/node135 均成功", "node134", "node135", report_node="node134"),
    sql("在 node135 异步生效复制集", PORT135,
        "SELECT fdd.replication_set_async_execute()", "异步生效",
        "replication_set_async_execute", report_node="node135"),
    sql("确认 node134 初始表为空", PORT134, "SELECT count(*) FROM %s" % TABLE,
        "返回 0", "0", report_node="node134"),
    sql("将 node135 insert_exists 设为 skip", PORT135,
        "SELECT fdd.alter_local_node_set_conflict_resolver('insert_exists','skip')",
        "返回 node135,insert_exists,skip", "node135,insert_exists,skip",
        report_node="node135"),
    sql("确认触发前 node135 没有 insert_exists 历史", PORT135,
        "SELECT count(*) FROM fdd.mmr_conflict_history WHERE relname='%s' AND conflict_type='insert_exists'" % TABLE,
        "返回 0", "0", report_node="node135"),
    mixed_transactions(),
    wait_for_rows("确认六个已提交冲突均写入历史、三条回滚不回放、三条正常数据同步", PORT135,
                  "SELECT (SELECT count(*) FROM %s WHERE id IN (1,3,5,7,9,11) AND name='a')::text || '|' || (SELECT count(*) FROM %s WHERE id BETWEEN 1000 AND 1002 AND name='normal-' || id::text)::text || '|' || (SELECT count(*) FROM fdd.mmr_conflict_history WHERE relname='%s' AND conflict_type='insert_exists' AND conflict_resolution='skip')::text" % (TABLE, TABLE, TABLE),
                  "6|3|6", "node135"),
]
