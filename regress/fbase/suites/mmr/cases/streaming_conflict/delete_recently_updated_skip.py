"""Streaming conflict document 2.6.1: delete_recently_updated with skip."""

from framework.assertions import command_succeeds
from framework.steps import command_step
from suites.mmr.cases.node_management.create_group import PSQL
from suites.mmr.cases.streaming_conflict.delete_missing_skip import (
    PORT134, PORT135, psql, sql, wait_for_rows,
)
from suites.mmr.cases.streaming_conflict.update_missing_insert_or_skip import (
    shared_non2pc_set_steps,
)
from suites.mmr.streaming_conflict_support import non2pc_case_base


def concurrent_sql(title, script, expected, display):
    step = command_step(title, ["sh", "-ec", script], expected,
                        command_succeeds(), node="mmr:mmr1", timeout=40,
                        display_sql=display, report_node="node134,node135")
    step["report_database"] = "postgres"
    return step


def ordered_delete_after_update(title, start, end):
    """Match mmr-autotest: node135 updates first; node134 deletes while it is open."""
    lock_key = 370000000 + start
    update = ("BEGIN; UPDATE immediate_parallel_conflict SET name=repeat('b',128) "
              "WHERE id BETWEEN %s AND %s; SELECT pg_advisory_xact_lock(%s); "
              "SELECT pg_sleep(3); COMMIT" % (start, end, lock_key))
    script = (
        "update_log=/tmp/fbase_regress_stream_recent_update_%s.log; rm -f \"$update_log\"; "
        "%s >\"$update_log\" 2>&1 & update_pid=$!; "
        "ready=; for attempt in $(seq 1 100); do "
        "if ! kill -0 \"$update_pid\" 2>/dev/null; then cat \"$update_log\"; wait \"$update_pid\"; exit 1; fi; "
        "ready=$(%s -X -At -h 127.0.0.1 -p %s -U postgres -d postgres -c %r 2>/dev/null || true); "
        "test \"$ready\" = f && break; sleep 0.1; done; "
        "test \"$ready\" = f || { echo 'node135 UPDATE transaction was not ready'; kill \"$update_pid\"; wait \"$update_pid\" || true; exit 1; }; "
        "%s; sleep 1; update_status=0; wait \"$update_pid\" || update_status=$?; cat \"$update_log\"; rm -f \"$update_log\"; exit \"$update_status\"" %
        (start, psql(PORT135, update), PSQL, PORT135,
         "SELECT pg_try_advisory_xact_lock(%s)" % lock_key,
         psql(PORT134, "DELETE FROM immediate_parallel_conflict WHERE id BETWEEN %s AND %s" % (start, end))))
    display = ("-- node135，先更新并保持事务\n%s;\n"
               "-- node134，node135 事务保持期间删除\n"
               "DELETE FROM immediate_parallel_conflict WHERE id BETWEEN %s AND %s;\n"
               "-- 等待 1 秒后 node135\nCOMMIT;" %
               ("BEGIN; UPDATE immediate_parallel_conflict SET name=repeat('b',128) WHERE id BETWEEN %s AND %s" % (start, end), start, end))
    return concurrent_sql(title, script,
                          "node135 UPDATE 已执行未提交后，node134 DELETE 成功；等待 1 秒后 node135 COMMIT",
                          display)


def build_non2pc_delete_recently_updated_case(case_id, name, section, resolver,
                                              start, expected_rows,
                                              expected_history, note):
    """Build a standalone 2.6 resolver case with document SQL kept visible."""
    end = start + 511
    case = non2pc_case_base(case_id, name, section, [
        "发布端 node134：debug_logical_replication_streaming=immediate，logical_decoding_work_mem=64kB。",
        "订阅端 node135：debug_logical_replication_streaming=buffered，logical_decoding_work_mem=64kB。",
        "两端 MMR streaming=parallel；node134 two_phase=false，node135 two_phase=true。",
        "本节 resolver：node135 的 delete_recently_updated=%s；%s" %
        (resolver, note),
    ])
    case["session"] = {
        "key": "mmr_streaming_conflict_non2pc",
        "order": {"skip": 150, "error": 160}[resolver],
        "fixtures": [{"type": "shared_mmr_conflict_topology", "data_dir":
                      "/tmp/fbase_regress_mmr_stream_delete_missing_{run_id}",
                      "source_port": PORT134, "target_port": PORT135}],
    }
    case["fixtures"] = [
        "cluster",
        {"type": "shared_mmr_conflict_reset", "source_port": PORT134,
         "target_port": PORT135, "set_name": "set1",
         "tables": ["immediate_parallel_conflict"],
         "resolvers": {"delete_recently_updated": "skip"}},
    ]
    case["steps"] = shared_non2pc_set_steps() + [
        sql("将 node135 delete_recently_updated 设为 %s" % resolver, PORT135,
            "SELECT fdd.alter_local_node_set_conflict_resolver('delete_recently_updated','%s')" % resolver,
            "返回 node135,delete_recently_updated,%s" % resolver,
            "node135,delete_recently_updated,%s" % resolver,
            report_node="node135"),
        sql("确认触发前 %s 冲突记录为 0" % resolver, PORT135,
            "SELECT count(*) FROM fdd.mmr_conflict_history WHERE relname='immediate_parallel_conflict' AND conflict_type='delete_recently_updated' AND conflict_resolution='%s'" % resolver,
            "返回 0", "0", report_node="node135"),
        sql("按文档在 node134 插入 512 行 delete_recently_updated 前置数据",
            PORT134,
            "INSERT INTO immediate_parallel_conflict(id,name) SELECT gs,repeat('a',128) FROM generate_series(%s,%s) gs" %
            (start, end),
            "返回 INSERT 0 512", "INSERT 0 512", report_node="node134"),
        wait_for_rows("等待 node135 收到 delete_recently_updated 前置的 512 行",
                      PORT135,
                      "SELECT count(*) FROM immediate_parallel_conflict WHERE id BETWEEN %s AND %s" %
                      (start, end), "512", "node135"),
        ordered_delete_after_update("按 mmr-autotest 时序执行 node135 UPDATE 与 node134 DELETE", start, end),
        wait_for_rows("确认 node135 的数据结果和 %s 冲突记录" % resolver,
                      PORT135,
                      "SELECT (SELECT count(*) FROM immediate_parallel_conflict WHERE id BETWEEN %s AND %s)::text || '|' || (SELECT count(*) FROM fdd.mmr_conflict_history WHERE relname='immediate_parallel_conflict' AND conflict_type='delete_recently_updated' AND conflict_resolution='%s')::text" %
                      (start, end, resolver),
                      "%s|%s" % (expected_rows, expected_history), "node135"),
    ]
    return case


CASE = build_non2pc_delete_recently_updated_case(
    "mmr.streaming_conflict.delete_recently_updated_skip",
    "streaming delete_recently_updated 的 skip 策略", "2.6.1", "skip",
    50001, "512", "512", "目标端保留刚更新的数据，远端 DELETE 被跳过。")
