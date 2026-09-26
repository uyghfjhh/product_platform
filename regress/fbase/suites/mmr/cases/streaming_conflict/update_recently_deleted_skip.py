"""Streaming conflict document 2.5.1: update_recently_deleted with skip."""

from framework.assertions import output_contains_text
from framework.steps import command_step
from suites.mmr.cases.node_management.create_group import PSQL
from suites.mmr.cases.streaming_conflict.delete_missing_skip import (
    PORT134, PORT135, sql, wait_for_rows,
)
from suites.mmr.cases.streaming_conflict.update_missing_insert_or_skip import (
    common_streaming_set_steps,
)
from suites.mmr.streaming_conflict_support import non2pc_case_base

def controlled_recent_delete(start, end):
    """Establish the DELETE -> UPDATE commit -> DELETE commit causality."""
    lock_key = 390000000 + start
    delete = ("BEGIN; DELETE FROM immediate_parallel_conflict WHERE id BETWEEN "
              "%s AND %s; SELECT pg_advisory_xact_lock(%s);" %
              (start, end, lock_key))
    update = ("BEGIN; SELECT pg_sleep(1); UPDATE immediate_parallel_conflict "
              "SET name=repeat('b',128) WHERE id BETWEEN %s AND %s; "
              "SELECT pg_sleep(6); COMMIT;" % (start, end))
    script = (
        "work=/tmp/fbase_regress_stream_recent_delete_%s; fifo=\"$work.stdin\"; "
        "delete_log=\"$work.delete.log\"; update_log=\"$work.update.log\"; "
        "rm -f \"$fifo\" \"$delete_log\" \"$update_log\"; mkfifo \"$fifo\"; "
        "%s -X -v ON_ERROR_STOP=1 -P pager=off -h 127.0.0.1 -p %s -U postgres -d postgres -f \"$fifo\" >\"$delete_log\" 2>&1 & delete_pid=$!; "
        "exec 3>\"$fifo\"; printf '%%s\\n' %r >&3; "
        "ready=; for attempt in $(seq 1 100); do "
        "if ! kill -0 \"$delete_pid\" 2>/dev/null; then cat \"$delete_log\"; wait \"$delete_pid\"; exit 1; fi; "
        "ready=$(%s -X -At -h 127.0.0.1 -p %s -U postgres -d postgres -c %r 2>/dev/null || true); "
        "test \"$ready\" = f && break; sleep 0.1; done; "
        "test \"$ready\" = f || { echo 'node135 DELETE transaction was not ready'; exec 3>&-; kill \"$delete_pid\"; wait \"$delete_pid\" || true; exit 1; }; "
        "%s -X -v ON_ERROR_STOP=1 -P pager=off -h 127.0.0.1 -p %s -U postgres -d postgres -c %r >\"$update_log\" 2>&1; update_status=$?; "
        "printf 'COMMIT;\\n' >&3; exec 3>&-; delete_status=0; wait \"$delete_pid\" || delete_status=$?; "
        "cat \"$update_log\" \"$delete_log\"; rm -f \"$fifo\" \"$delete_log\" \"$update_log\"; "
        "test \"$update_status\" = 0 && test \"$delete_status\" = 0" %
        (start, PSQL, PORT135, delete, PSQL, PORT135,
         "SELECT pg_try_advisory_xact_lock(%s)" % lock_key,
         PSQL, PORT134, update))
    step = command_step(
        "按 mmr-autotest 时序执行 node135 DELETE 与 node134 UPDATE",
        ["sh", "-ec", script],
        "确认 node135 DELETE 已执行并保持；node134 UPDATE 在第 1 秒开始、第 7 秒提交；随后 node135 COMMIT",
        output_contains_text("DELETE 512", "UPDATE 512", "COMMIT"),
        node="mmr:mmr1", timeout=40,
        display_sql=(
            "-- node135，DELETE 后持有事务锁\n%s\n"
            "-- 确认 node135 DELETE 已执行后，node134 1 秒后 UPDATE、6 秒后 COMMIT\n%s\n"
            "-- node134 UPDATE 成功提交后，node135\nCOMMIT;" % (delete, update)),
        report_node="node134,node135")
    step["report_database"] = "postgres"
    return step


def build_update_recently_deleted_case(case_id, name, section, resolver, start,
                                       final_rows, history_rows,
                                       combined_history=False):
    end = start + 511
    history_count = ("SELECT count(*) FROM fdd.mmr_conflict_history "
                     "WHERE relname='immediate_parallel_conflict' "
                     "AND conflict_type='update_recently_deleted' "
                     "AND conflict_resolution='%s'" % resolver)
    history_value = ("((%s) > 0)::text" % history_count if history_rows is None
                     else "(%s)::text" % history_count)
    case = non2pc_case_base(case_id, name, section, [
        "发布端 node134：debug_logical_replication_streaming=immediate，logical_decoding_work_mem=64kB。",
        "订阅端 node135：debug_logical_replication_streaming=buffered，logical_decoding_work_mem=64kB。",
        "两端 MMR streaming=parallel；node134 two_phase=false，node135 two_phase=true。",
        "本节 resolver：node135 的 update_recently_deleted=%s；node135 DELETE 保持未提交，node134 UPDATE 完成后再提交 DELETE。" % resolver,
    ])
    if combined_history:
        verification = wait_for_rows(
            "确认 node135 数据行和 %s 历史合计覆盖 512 行" % resolver, PORT135,
            ("SELECT ((SELECT count(*) FROM immediate_parallel_conflict WHERE id BETWEEN %s AND %s) + (%s))::text || '|' || ((%s) > 0)::text" %
             (start, end, history_count, history_count)),
            "512|true", "node135")
    else:
        verification = wait_for_rows(
            "确认 node135 的数据行数和 %s 冲突记录" % resolver, PORT135,
            ("SELECT (SELECT count(*) FROM immediate_parallel_conflict WHERE id BETWEEN %s AND %s)::text || '|' || %s" %
             (start, end, history_value)),
            "%s|%s" % (final_rows, history_rows if history_rows is not None else "true"),
            "node135")
    case["steps"] = common_streaming_set_steps() + [
    sql("将 node135 update_recently_deleted 设为 %s" % resolver, PORT135,
        "SELECT fdd.alter_local_node_set_conflict_resolver('update_recently_deleted','%s')" % resolver,
        "返回 node135,update_recently_deleted,%s" % resolver,
        "node135,update_recently_deleted,%s" % resolver, report_node="node135"),
    sql("确认触发前 %s 冲突记录为 0" % resolver, PORT135,
        "SELECT count(*) FROM fdd.mmr_conflict_history WHERE relname='immediate_parallel_conflict' AND conflict_type='update_recently_deleted' AND conflict_resolution='%s'" % resolver,
        "返回 0", "0", report_node="node135"),
    sql("按文档在 node134 插入 512 行并等待同步", PORT134,
        "INSERT INTO immediate_parallel_conflict(id,name) SELECT gs,repeat('a',128) FROM generate_series(%s,%s) gs" % (start, end),
        "返回 INSERT 0 512", "INSERT 0 512", report_node="node134"),
    wait_for_rows("等待 node135 收到 update_recently_deleted 前置的 512 行", PORT135,
                  "SELECT count(*) FROM immediate_parallel_conflict WHERE id BETWEEN %s AND %s" % (start, end),
                  "512", "node135"),
    controlled_recent_delete(start, end),
    verification,
    ]
    return case


CASE = build_update_recently_deleted_case(
    "mmr.streaming_conflict.update_recently_deleted_skip",
    "streaming update_recently_deleted 的 skip 策略", "2.5.1", "skip",
    60001, None, None, combined_history=True)
