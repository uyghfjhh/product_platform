"""Reusable 2PC streaming-conflict case builders.

Kept outside ``cases`` so discovery never mistakes it for an executable case.
"""

from suites.mmr.cases.streaming_conflict.delete_missing_skip import PORT134, PORT135
from suites.mmr.cases.streaming_conflict.update_missing_insert_or_skip import common_streaming_set_steps
from copy import deepcopy

from framework.assertions import output_contains_text
from suites.mmr.streaming_conflict_support import (PSQL, non2pc_case_base, shell,
                                                   sql, wait_for_rows)


TABLE = "immediate_parallel_2pc"


def parallel_prepared_update(title, start, gid, action):
    """Run the document's simultaneous two-node PK update and resolve both GIDs."""
    statement = (
        "node134: BEGIN; UPDATE immediate_parallel_2pc SET id=id+1000000 "
        "WHERE id BETWEEN {start} AND {end}; SELECT pg_sleep(2); "
        "PREPARE TRANSACTION '{gid}'; {action} PREPARED '{gid}';\n"
        "node135: BEGIN; UPDATE immediate_parallel_2pc SET id=id+1000000 "
        "WHERE id BETWEEN {start} AND {end}; SELECT pg_sleep(2); "
        "PREPARE TRANSACTION '{gid}'; {action} PREPARED '{gid}';"
    ).format(start=start, end=start + 511, gid=gid, action=action)
    node_sql = ("BEGIN; UPDATE immediate_parallel_2pc SET id=id+1000000 WHERE id BETWEEN {start} AND {end}; "
                "SELECT pg_sleep(2); PREPARE TRANSACTION '{gid}'").format(
                    start=start, end=start + 511, gid=gid)
    command = (
        "(%s -X -P pager=off -h 127.0.0.1 -p %s -U postgres -d postgres -c %r; "
        "%s -X -P pager=off -h 127.0.0.1 -p %s -U postgres -d postgres -c %r) & p1=$!; "
        "(%s -X -P pager=off -h 127.0.0.1 -p %s -U postgres -d postgres -c %r; "
        "%s -X -P pager=off -h 127.0.0.1 -p %s -U postgres -d postgres -c %r) & p2=$!; "
        "wait $p1; wait $p2" %
        (PSQL, PORT134, node_sql, PSQL, PORT134, "%s PREPARED '%s'" % (action, gid),
         PSQL, PORT135, node_sql, PSQL, PORT135, "%s PREPARED '%s'" % (action, gid)))
    step = shell(title, command, "两端均返回 UPDATE 512、PREPARE TRANSACTION 和 %s PREPARED" % action,
                 output_contains_text("UPDATE 512", "PREPARE TRANSACTION", "%s PREPARED" % action), timeout=30)
    step["display_sql"] = statement
    step["report_node"] = "node134/node135"
    return step


def two_phase_update_pkey_update_if_newer_case():
    """Build 3.4.1 with its two-node concurrent two-phase update flow."""
    start, end, shifted_start, shifted_end = 70001, 70512, 1070001, 1070512
    case = non2pc_case_base(
        "mmr.streaming_conflict.two_phase_update_pkey_exists_update_if_newer",
        "2PC streaming update_pkey_exists 的 update_if_newer 策略", "3.4.1", [
            "node134=parallel,two_phase=false；node135=parallel,two_phase=true。",
            "node134=immediate、node135=buffered，logical_decoding_work_mem=64kB。",
            "node135 的 update_pkey_exists=update_if_newer；两个节点并发更新同一批 512 个主键。",
        ])
    case["steps"] = two_phase_streaming_set_steps() + [
        sql("按文档在 node134 创建 2PC 测试表", PORT134,
            "CREATE TABLE %s(id int PRIMARY KEY,name text)" % TABLE,
            "返回 CREATE TABLE", "CREATE TABLE", report_node="node134"),
        sql("按文档在 node135 创建空 2PC 测试表", PORT135,
            "CREATE TABLE %s(id int PRIMARY KEY,name text)" % TABLE,
            "返回 CREATE TABLE", "CREATE TABLE", report_node="node135"),
        sql("按文档在 node135 将 2PC 表加入 set1", PORT135,
            "SELECT fdd.replication_set_add_table('%s'::regclass,'set1',false,false)" % TABLE,
            "复制集绑定成功", "replication_set_add_table", report_node="node135"),
        sql("按文档在所有节点订阅 set1", PORT134,
            "SELECT fdd.run_on_all_nodes('select fdd.alter_node_replication_sets(''{set1}'');')",
            "node134/node135 均成功", "node134", "node135", report_node="node134"),
        sql("按文档在 node135 异步执行复制集变更", PORT135,
            "SELECT fdd.replication_set_async_execute()", "异步生效", "replication_set_async_execute", report_node="node135"),
        sql("将 node135 update_pkey_exists 设为 update_if_newer", PORT135,
            "SELECT fdd.alter_local_node_set_conflict_resolver('update_pkey_exists','update_if_newer')",
            "返回 node135,update_pkey_exists,update_if_newer", "node135,update_pkey_exists,update_if_newer", report_node="node135"),
        sql("确认触发前 update_if_newer 冲突记录为 0", PORT135,
            "SELECT count(*) FROM fdd.mmr_conflict_history WHERE relname='%s' AND conflict_type='update_pkey_exists' AND conflict_resolution='update_if_newer'" % TABLE,
            "返回 0", "0", report_node="node135"),
        sql("按文档在 node134 插入 512 行主键更新前置数据", PORT134,
            "INSERT INTO %s(id,name) SELECT gs,repeat('a',128) FROM generate_series(%s,%s) gs" % (TABLE, start, end),
            "返回 INSERT 0 512", "INSERT 0 512", report_node="node134"),
        wait_for_rows("等待 node135 收到 512 行主键更新前置数据", PORT135,
                      "SELECT count(*) FROM %s WHERE id BETWEEN %s AND %s" % (TABLE, start, end), "512", "node135"),
        parallel_prepared_update("按文档并发准备并回滚两端主键更新", start, "stream_70", "ROLLBACK"),
        wait_for_rows("确认 rollback 后 node135 保留旧主键、无新主键且无冲突历史", PORT135,
                      "SELECT (SELECT count(*) FROM %s WHERE id BETWEEN %s AND %s)::text || '|' || "
                      "(SELECT count(*) FROM %s WHERE id BETWEEN %s AND %s)::text || '|' || "
                      "(SELECT count(*) FROM fdd.mmr_conflict_history WHERE relname='%s' AND conflict_type='update_pkey_exists' AND conflict_resolution='update_if_newer')::text" %
                      (TABLE,start,end,TABLE,shifted_start,shifted_end,TABLE), "512|0|0", "node135"),
        parallel_prepared_update("按文档并发准备并提交两端主键更新", start, "stream_70", "COMMIT"),
        wait_for_rows("确认 commit 后 node135 新主键为 512 行并记录 512 条冲突", PORT135,
                      "SELECT (SELECT count(*) FROM %s WHERE id BETWEEN %s AND %s)::text || '|' || "
                      "(SELECT count(*) FROM %s WHERE id BETWEEN %s AND %s)::text || '|' || "
                      "(SELECT count(*) FROM fdd.mmr_conflict_history WHERE relname='%s' AND conflict_type='update_pkey_exists' AND conflict_resolution='update_if_newer')::text" %
                      (TABLE,start,end,TABLE,shifted_start,shifted_end,TABLE), "0|512|512", "node135"),
    ]
    return case


def two_phase_update_pkey_case(case_id, name, section, resolver, start, gid,
                               rollback_history, commit_history,
                               history_pattern=None):
    """Specialize the verified 3.4 concurrency flow for one resolver."""
    case = deepcopy(two_phase_update_pkey_update_if_newer_case())
    case.update({"id": case_id, "name": name, "section": section})
    end, shifted_start, shifted_end = start + 511, start + 1000000, start + 1000511
    replacements = {
        "update_if_newer": resolver,
        "70001": str(start), "70512": str(end),
        "1070001": str(shifted_start), "1070512": str(shifted_end),
        "stream_70": gid,
    }
    for step in case["steps"]:
        for key in ("title", "display_sql", "expected"):
            if isinstance(step.get(key), str):
                for old, new in replacements.items():
                    step[key] = step[key].replace(old, new)
        if isinstance(step.get("argv"), list):
            step["argv"] = [
                value if not isinstance(value, str) else _replace_text(value, replacements)
                for value in step["argv"]
            ]
    if history_pattern:
        for step in case["steps"]:
            for key in ("display_sql",):
                if isinstance(step.get(key), str):
                    step[key] = step[key].replace(
                        "conflict_resolution='%s'" % resolver,
                        "conflict_resolution LIKE '%s'" % history_pattern)
            if isinstance(step.get("argv"), list):
                step["argv"] = [
                    value.replace("conflict_resolution='%s'" % resolver,
                                  "conflict_resolution LIKE '%s'" % history_pattern)
                    if isinstance(value, str) else value for value in step["argv"]
                ]
    case["conflict_configuration"][-1] = (
        "node135 的 update_pkey_exists=%s；两个节点并发更新同一批 512 个主键。" % resolver)
    for step in case["steps"]:
        if step["title"] == "将 node135 update_pkey_exists 设为 %s" % resolver:
            step["expected"] = "返回 node135,update_pkey_exists,%s" % resolver
            step["assertion"] = output_contains_text(
                "node135,update_pkey_exists,%s" % resolver)
        if step["title"].startswith("确认 rollback 后"):
            step["title"] = "确认 rollback 后 node135 保留旧主键、无新主键且历史为 %s" % rollback_history
            step["expected"] = "45 秒内返回 512|0|%s" % rollback_history
            step["argv"][-1] = step["argv"][-1].replace("512|0|0", "512|0|%s" % rollback_history)
        if step["title"].startswith("确认 commit 后"):
            step["title"] = "确认 commit 后 node135 新主键为 512 行且历史为 %s" % commit_history
            step["expected"] = "45 秒内返回 0|512|%s" % commit_history
            step["argv"][-1] = step["argv"][-1].replace("0|512|512", "0|512|%s" % commit_history)
    return case


def _replace_text(value, replacements):
    for old, new in replacements.items():
        value = value.replace(old, new)
    return value


def parallel_prepared_recent_delete(title, start, gid, action):
    """Run the 2PC form of mmr-autotest's delete-then-update timing.

    The node135 DELETE stays open while node134's UPDATE is resolved.  That
    gives the subscriber a deterministic deleted-row transition instead of
    relying on two background psql processes reaching PREPARE at similar times.
    """
    end = start + 511
    delete_begin = "BEGIN; DELETE FROM immediate_parallel_2pc WHERE id BETWEEN %s AND %s;" % (start, end)
    delete_prepare = "PREPARE TRANSACTION '%s'" % gid
    delete_finish = "%s PREPARED '%s'" % (action, gid)
    update_prepare = ("BEGIN; UPDATE immediate_parallel_2pc SET name=repeat('b',128) "
                      "WHERE id BETWEEN %s AND %s; PREPARE TRANSACTION '%s';" %
                      (start, end, gid))
    update_finish = "%s PREPARED '%s'" % (action, gid)
    lock_key = 310000000 + start
    statement = (
        "node135: %s -- 保持 DELETE 未提交，等待 node134 完成预备事务\n"
        "node134: %s %s; -- 等待 1 秒使远端 UPDATE 到达 node135\n"
        "node135: %s;" %
        (delete_begin, update_prepare, update_finish, delete_finish))
    target_sql = "%s SELECT pg_advisory_xact_lock(%s); SELECT pg_sleep(4); %s" % (
        delete_begin, lock_key, delete_prepare)
    command = (
        "target_log=/tmp/fbase_regress_recent_delete_%s_%s_%s.log; rm -f \"$target_log\"; "
        "(%s -X -v ON_ERROR_STOP=1 -P pager=off -h 127.0.0.1 -p %s -U postgres -d postgres -c %r >\"$target_log\" 2>&1 && "
        "%s -X -v ON_ERROR_STOP=1 -P pager=off -h 127.0.0.1 -p %s -U postgres -d postgres -c %r >>\"$target_log\" 2>&1) & target_pid=$!; "
        "ready=; for attempt in $(seq 1 100); do "
        "if ! kill -0 \"$target_pid\" 2>/dev/null; then cat \"$target_log\"; wait \"$target_pid\"; exit 1; fi; "
        "ready=$(%s -X -At -h 127.0.0.1 -p %s -U postgres -d postgres -c %r 2>/dev/null || true); "
        "test \"$ready\" = f && break; sleep 0.1; done; "
        "test \"$ready\" = f || { echo 'node135 DELETE transaction was not ready'; kill \"$target_pid\"; wait \"$target_pid\" || true; exit 1; }; "
        "%s -X -v ON_ERROR_STOP=1 -P pager=off -h 127.0.0.1 -p %s -U postgres -d postgres -c %r; "
        "%s -X -v ON_ERROR_STOP=1 -P pager=off -h 127.0.0.1 -p %s -U postgres -d postgres -c %r; "
        "sleep 1; target_status=0; wait \"$target_pid\" || target_status=$?; cat \"$target_log\"; rm -f \"$target_log\"; exit \"$target_status\"" %
        (gid, action.lower(), start, PSQL, PORT135, target_sql, PSQL, PORT135, delete_finish,
         PSQL, PORT135, "SELECT pg_try_advisory_xact_lock(%s)" % lock_key,
         PSQL, PORT134, update_prepare, PSQL, PORT134, update_finish))
    step = shell(title, command,
                 "node135 DELETE 保持未提交，node134 UPDATE 先完成 %s PREPARED，随后 node135 完成 %s PREPARED" %
                 (action, action),
                 output_contains_text("DELETE 512", "UPDATE 512", "PREPARE TRANSACTION",
                                      "%s PREPARED" % action), timeout=40)
    step["display_sql"] = statement
    step["report_node"] = "node134/node135"
    return step


def two_phase_update_recently_deleted_case(case_id, name, section, resolver, start,
                                           gid, final_rows, final_history,
                                           history_pattern=None):
    """Build one isolated 3.5 concurrent delete/update resolver case."""
    history_pattern = history_pattern or resolver
    history_filter = "conflict_resolution LIKE '%s'" % history_pattern if "%" in history_pattern else "conflict_resolution='%s'" % history_pattern
    history_sql = ("SELECT count(*) FROM fdd.mmr_conflict_history WHERE relname='%s' "
                   "AND conflict_type='update_recently_deleted' AND %s" % (TABLE, history_filter))
    resolution_summary_sql = (
        "SELECT conflict_type,conflict_resolution,count(*) FROM fdd.mmr_conflict_history "
        "WHERE relname='%s' AND conflict_type IN ('update_recently_deleted','update_missing') "
        "GROUP BY conflict_type,conflict_resolution ORDER BY conflict_type,conflict_resolution" % TABLE)
    history_started_sql = (
        "SELECT (count(*) > 0)::int FROM fdd.mmr_conflict_history WHERE relname='%s' "
        "AND conflict_type IN ('update_recently_deleted','update_missing')" % TABLE)
    end = start + 511
    case = non2pc_case_base(case_id, name, section, [
        "node134=parallel,two_phase=false；node135=parallel,two_phase=true。",
        "node134=immediate、node135=buffered，logical_decoding_work_mem=64kB。",
        "node135 的 update_recently_deleted=%s；node135 删除与 node134 更新同一批 512 行。" % resolver,
    ])
    case["steps"] = two_phase_streaming_set_steps() + [
        sql("按文档在 node134 创建 2PC 测试表", PORT134,
            "CREATE TABLE %s(id int PRIMARY KEY,name text)" % TABLE,
            "返回 CREATE TABLE", "CREATE TABLE", report_node="node134"),
        sql("按文档在 node135 创建空 2PC 测试表", PORT135,
            "CREATE TABLE %s(id int PRIMARY KEY,name text)" % TABLE,
            "返回 CREATE TABLE", "CREATE TABLE", report_node="node135"),
        sql("按文档在 node135 将 2PC 表加入 set1", PORT135,
            "SELECT fdd.replication_set_add_table('%s'::regclass,'set1',false,false)" % TABLE,
            "复制集绑定成功", "replication_set_add_table", report_node="node135"),
        sql("按文档在所有节点订阅 set1", PORT134,
            "SELECT fdd.run_on_all_nodes('select fdd.alter_node_replication_sets(''{set1}'');')",
            "node134/node135 均成功", "node134", "node135", report_node="node134"),
        sql("按文档在 node135 异步执行复制集变更", PORT135,
            "SELECT fdd.replication_set_async_execute()", "异步生效", "replication_set_async_execute", report_node="node135"),
        sql("将 node135 update_recently_deleted 设为 %s" % resolver, PORT135,
            "SELECT fdd.alter_local_node_set_conflict_resolver('update_recently_deleted','%s')" % resolver,
            "返回 node135,update_recently_deleted,%s" % resolver,
            "node135,update_recently_deleted,%s" % resolver, report_node="node135"),
        sql("确认触发前 %s 冲突记录为 0" % resolver, PORT135, history_sql,
            "返回 0", "0", report_node="node135"),
        sql("按文档在 node134 插入 512 行前置数据", PORT134,
            "INSERT INTO %s(id,name) SELECT gs,repeat('a',128) FROM generate_series(%s,%s) gs" % (TABLE,start,end),
            "返回 INSERT 0 512", "INSERT 0 512", report_node="node134"),
        wait_for_rows("等待 node135 收到 512 行前置数据", PORT135,
                      "SELECT count(*) FROM %s WHERE id BETWEEN %s AND %s" % (TABLE,start,end), "512", "node135"),
        parallel_prepared_recent_delete("按文档并发准备并回滚 node135 删除和 node134 更新", start, gid, "ROLLBACK"),
        wait_for_rows("确认 rollback 后 node135 保留 512 行且无冲突历史", PORT135,
                      "SELECT (SELECT count(*) FROM %s WHERE id BETWEEN %s AND %s)::text || '|' || (%s)::text" %
                      (TABLE,start,end,history_sql), "512|0", "node135"),
        parallel_prepared_recent_delete("按文档并发准备并提交 node135 删除和 node134 更新", start, gid, "COMMIT"),
        wait_for_rows("等待 node135 开始写入 delete/update 冲突历史", PORT135,
                      history_started_sql, "1", "node135"),
        sql("展示提交后 update_recently_deleted 与 update_missing 的实际分支", PORT135,
            resolution_summary_sql,
            "显示冲突类型、决议方式和条数", "conflict_type", report_node="node135"),
        wait_for_rows("确认 commit 后 node135 数据和冲突历史", PORT135,
                      "SELECT (SELECT count(*) FROM %s WHERE id BETWEEN %s AND %s)::text || '|' || (%s)::text" %
                      (TABLE,start,end,history_sql), "%s|%s" % (final_rows, final_history), "node135"),
    ]
    return case


def parallel_prepared_recent_update(title, start, gid, action):
    """Run mmr-autotest's confirmed node135-update then node134-delete flow."""
    end = start + 511
    lock_key = 320000000 + start
    delete_prepare = ("BEGIN; DELETE FROM immediate_parallel_2pc WHERE id BETWEEN %s AND %s; "
                      "PREPARE TRANSACTION '%s'" % (start, end, gid))
    update_begin = ("BEGIN; UPDATE immediate_parallel_2pc SET name=repeat('b',128) "
                    "WHERE id BETWEEN %s AND %s;" % (start, end))
    update_prepare = "PREPARE TRANSACTION '%s'" % gid
    update_finish = "%s PREPARED '%s'" % (action, gid)
    statement = ("node135: %s -- 保持 UPDATE 未提交，等待 node134 DELETE 完成\n"
                 "node134: %s; %s PREPARED '%s';\nnode135: %s;" %
                 (update_begin, delete_prepare, action, gid, update_finish))
    target_sql = "%s SELECT pg_advisory_xact_lock(%s); SELECT pg_sleep(4); %s" % (
        update_begin, lock_key, update_prepare)
    command = (
        "target_log=/tmp/fbase_regress_recent_update_%s_%s_%s.log; rm -f \"$target_log\"; "
        "(%s -X -v ON_ERROR_STOP=1 -P pager=off -h 127.0.0.1 -p %s -U postgres -d postgres -c %r >\"$target_log\" 2>&1 && "
        "%s -X -v ON_ERROR_STOP=1 -P pager=off -h 127.0.0.1 -p %s -U postgres -d postgres -c %r >>\"$target_log\" 2>&1) & target_pid=$!; "
        "ready=; for attempt in $(seq 1 100); do "
        "if ! kill -0 \"$target_pid\" 2>/dev/null; then cat \"$target_log\"; wait \"$target_pid\"; exit 1; fi; "
        "ready=$(%s -X -At -h 127.0.0.1 -p %s -U postgres -d postgres -c %r 2>/dev/null || true); "
        "test \"$ready\" = f && break; sleep 0.1; done; "
        "test \"$ready\" = f || { echo 'node135 UPDATE transaction was not ready'; kill \"$target_pid\"; wait \"$target_pid\" || true; exit 1; }; "
        "%s -X -v ON_ERROR_STOP=1 -P pager=off -h 127.0.0.1 -p %s -U postgres -d postgres -c %r; "
        "%s -X -v ON_ERROR_STOP=1 -P pager=off -h 127.0.0.1 -p %s -U postgres -d postgres -c %r; "
        "sleep 1; target_status=0; wait \"$target_pid\" || target_status=$?; cat \"$target_log\"; rm -f \"$target_log\"; exit \"$target_status\"" %
        (gid, action.lower(), start, PSQL, PORT135, target_sql, PSQL, PORT135, update_finish,
         PSQL, PORT135, "SELECT pg_try_advisory_xact_lock(%s)" % lock_key,
         PSQL, PORT134, delete_prepare, PSQL, PORT134, "%s PREPARED '%s'" % (action, gid)))
    step = shell(title, command, "两端均返回 PREPARE TRANSACTION 和 %s PREPARED" % action,
                 output_contains_text("PREPARE TRANSACTION", "%s PREPARED" % action), timeout=30)
    step["display_sql"] = statement
    step["report_node"] = "node134/node135"
    return step


def parallel_prepared_origin_change(title, source_port, observer_port, table, start,
                                    gid, action):
    """Run document 3.11's ordered node134/node136 2PC updates."""
    end = start + 511
    source_sql = ("BEGIN; UPDATE %s SET name=repeat('d',128) WHERE id BETWEEN %s AND %s; "
                  "SELECT pg_sleep(2); PREPARE TRANSACTION '%s'" % (table, start, end, gid))
    observer_sql = ("BEGIN; SELECT pg_sleep(1); UPDATE %s SET name=repeat('e',128) "
                    "WHERE id BETWEEN %s AND %s; PREPARE TRANSACTION '%s'" %
                    (table, start, end, gid))
    statement = ("node134: %s; %s PREPARED '%s';\nnode136: %s; %s PREPARED '%s';" %
                 (source_sql, action, gid, observer_sql, action, gid))
    command = (
        "(%s -X -P pager=off -h 127.0.0.1 -p %s -U postgres -d postgres -c %r; "
        "%s -X -P pager=off -h 127.0.0.1 -p %s -U postgres -d postgres -c %r) & p1=$!; "
        "(%s -X -P pager=off -h 127.0.0.1 -p %s -U postgres -d postgres -c %r; "
        "%s -X -P pager=off -h 127.0.0.1 -p %s -U postgres -d postgres -c %r) & p2=$!; wait $p1; wait $p2" %
        (PSQL, source_port, source_sql, PSQL, source_port, "%s PREPARED '%s'" % (action, gid),
         PSQL, observer_port, observer_sql, PSQL, observer_port, "%s PREPARED '%s'" % (action, gid)))
    step = shell(title, command, "两端均返回 PREPARE TRANSACTION 和 %s PREPARED" % action,
                 output_contains_text("PREPARE TRANSACTION", "%s PREPARED" % action), timeout=30)
    step["display_sql"] = statement
    step["report_node"] = "node134/node136"
    return step


def two_phase_delete_recently_updated_case(case_id, name, section, resolver, start,
                                           gid, final_rows, final_history,
                                           history_pattern=None):
    """Build one isolated 3.6 concurrent delete/update resolver case."""
    history_pattern = history_pattern or resolver
    history_filter = "conflict_resolution LIKE '%s'" % history_pattern if "%" in history_pattern else "conflict_resolution='%s'" % history_pattern
    history_sql = ("SELECT count(*) FROM fdd.mmr_conflict_history WHERE relname='%s' "
                   "AND conflict_type='delete_recently_updated' AND %s" % (TABLE, history_filter))
    end = start + 511
    case = non2pc_case_base(case_id, name, section, [
        "node134=parallel,two_phase=false；node135=parallel,two_phase=true。",
        "node134=immediate、node135=buffered，logical_decoding_work_mem=64kB。",
        "node135 的 delete_recently_updated=%s；node134 删除与 node135 更新同一批 512 行。" % resolver,
    ])
    case["steps"] = two_phase_streaming_set_steps() + [
        sql("按文档在 node134 创建 2PC 测试表", PORT134, "CREATE TABLE %s(id int PRIMARY KEY,name text)" % TABLE, "返回 CREATE TABLE", "CREATE TABLE", report_node="node134"),
        sql("按文档在 node135 创建空 2PC 测试表", PORT135, "CREATE TABLE %s(id int PRIMARY KEY,name text)" % TABLE, "返回 CREATE TABLE", "CREATE TABLE", report_node="node135"),
        sql("按文档在 node135 将 2PC 表加入 set1", PORT135, "SELECT fdd.replication_set_add_table('%s'::regclass,'set1',false,false)" % TABLE, "复制集绑定成功", "replication_set_add_table", report_node="node135"),
        sql("按文档在所有节点订阅 set1", PORT134, "SELECT fdd.run_on_all_nodes('select fdd.alter_node_replication_sets(''{set1}'');')", "node134/node135 均成功", "node134", "node135", report_node="node134"),
        sql("按文档在 node135 异步执行复制集变更", PORT135, "SELECT fdd.replication_set_async_execute()", "异步生效", "replication_set_async_execute", report_node="node135"),
        sql("将 node135 delete_recently_updated 设为 %s" % resolver, PORT135, "SELECT fdd.alter_local_node_set_conflict_resolver('delete_recently_updated','%s')" % resolver, "返回 node135,delete_recently_updated,%s" % resolver, "node135,delete_recently_updated,%s" % resolver, report_node="node135"),
        sql("确认触发前 %s 冲突记录为 0" % resolver, PORT135, history_sql, "返回 0", "0", report_node="node135"),
        sql("按文档在 node134 插入 512 行前置数据", PORT134, "INSERT INTO %s(id,name) SELECT gs,repeat('a',128) FROM generate_series(%s,%s) gs" % (TABLE,start,end), "返回 INSERT 0 512", "INSERT 0 512", report_node="node134"),
        wait_for_rows("等待 node135 收到 512 行前置数据", PORT135, "SELECT count(*) FROM %s WHERE id BETWEEN %s AND %s" % (TABLE,start,end), "512", "node135"),
        parallel_prepared_recent_update("按文档并发准备并回滚 node134 删除和 node135 更新", start, gid, "ROLLBACK"),
        wait_for_rows("确认 rollback 后 node135 保留 512 行且无冲突历史", PORT135, "SELECT (SELECT count(*) FROM %s WHERE id BETWEEN %s AND %s)::text || '|' || (%s)::text" % (TABLE,start,end,history_sql), "512|0", "node135"),
        parallel_prepared_recent_update("按文档并发准备并提交 node134 删除和 node135 更新", start, gid, "COMMIT"),
        wait_for_rows("确认 commit 后 node135 数据和冲突历史", PORT135, "SELECT (SELECT count(*) FROM %s WHERE id BETWEEN %s AND %s)::text || '|' || (%s)::text" % (TABLE,start,end,history_sql), "%s|%s" % (final_rows, final_history), "node135"),
    ]
    return case


def two_phase_target_column_ignore_if_null_case():
    """Build 3.7.1's asymmetric-column two-phase flow."""
    table = "immediate_parallel_target_column_2pc"
    history = ("SELECT count(*) FROM fdd.mmr_conflict_history WHERE relname='%s' "
               "AND conflict_type='target_column_missing' AND conflict_resolution LIKE 'ignore_if_null%%'" % table)
    case = non2pc_case_base(
        "mmr.streaming_conflict.two_phase_target_column_missing_ignore_if_null",
        "2PC streaming target_column_missing 的 ignore_if_null 策略", "3.7.1", [
            "node134=parallel,two_phase=false；node135=parallel,two_phase=true。",
            "node134=immediate、node135=buffered，logical_decoding_work_mem=64kB。",
            "node134 表含 city 列，node135 缺 city；node135 的 target_column_missing=ignore_if_null。",
        ])
    case["steps"] = two_phase_streaming_set_steps() + [
        sql("按文档在 node134 创建含 city 的测试表", PORT134,
            "CREATE TABLE %s(id int PRIMARY KEY,name name,city text DEFAULT 'changsha')" % table,
            "返回 CREATE TABLE", "CREATE TABLE", report_node="node134"),
        sql("按文档在 node135 创建缺 city 的测试表", PORT135,
            "CREATE TABLE %s(id int PRIMARY KEY,name name)" % table,
            "返回 CREATE TABLE", "CREATE TABLE", report_node="node135"),
        sql("按文档放行该表的节点结构差异", PORT135,
            "SELECT fdd.alter_mmr_check_node_conf($${\"public.%s\"}$$)" % table,
            "返回 t", "t", report_node="node135"),
        sql("按文档在 node135 将异构表加入 set1", PORT135,
            "SELECT fdd.replication_set_add_table('%s'::regclass,'set1',false,false)" % table,
            "复制集绑定成功", "replication_set_add_table", report_node="node135"),
        sql("按文档在所有节点订阅 set1", PORT134,
            "SELECT fdd.run_on_all_nodes('select fdd.alter_node_replication_sets(''{set1}'');')",
            "node134/node135 均成功", "node134", "node135", report_node="node134"),
        sql("按文档在 node135 异步执行复制集变更", PORT135,
            "SELECT fdd.replication_set_async_execute()", "异步生效", "replication_set_async_execute", report_node="node135"),
        sql("将 node135 target_column_missing 设为 ignore_if_null", PORT135,
            "SELECT fdd.alter_local_node_set_conflict_resolver('target_column_missing','ignore_if_null')",
            "返回 node135,target_column_missing,ignore_if_null", "node135,target_column_missing,ignore_if_null", report_node="node135"),
        sql("确认触发前 ignore_if_null 历史为 0", PORT135, history, "返回 0", "0", report_node="node135"),
        sql("按文档准备第一次含 city 的 512 行插入", PORT134,
            "BEGIN; INSERT INTO %s(id,name,city) SELECT gs,repeat('a',128),'changsha' FROM generate_series(100001,100512) gs; PREPARE TRANSACTION 'stream_100'" % table,
            "返回 BEGIN、INSERT 0 512、PREPARE TRANSACTION", "BEGIN", "INSERT 0 512", "PREPARE TRANSACTION", report_node="node134"),
        sql("按文档回滚第一次预备事务", PORT134, "ROLLBACK PREPARED 'stream_100'", "返回 ROLLBACK PREPARED", "ROLLBACK PREPARED", report_node="node134"),
        wait_for_rows("确认 rollback 后无目标行且有一条 GID 历史", PORT135,
                      "SELECT (SELECT count(*) FROM %s WHERE id BETWEEN 100001 AND 100512)::text || '|' || (%s)::text" % (table,history), "0|1", "node135"),
        sql("按文档准备第二次含 city 的 512 行插入", PORT134,
            "BEGIN; INSERT INTO %s(id,name,city) SELECT gs,repeat('a',128),'changsha' FROM generate_series(100001,100512) gs; PREPARE TRANSACTION 'stream_100'" % table,
            "返回 BEGIN、INSERT 0 512、PREPARE TRANSACTION", "BEGIN", "INSERT 0 512", "PREPARE TRANSACTION", report_node="node134"),
        sql("按文档提交第二次预备事务", PORT134, "COMMIT PREPARED 'stream_100'", "返回 COMMIT PREPARED", "COMMIT PREPARED", report_node="node134"),
        wait_for_rows("确认 commit 后无目标行且累计两条 streaming GID 历史", PORT135,
                      "SELECT (SELECT count(*) FROM %s WHERE id BETWEEN 100001 AND 100512)::text || '|' || (%s)::text" % (table,history), "0|2", "node135"),
        sql("按文档恢复 node135 表结构", PORT135,
            "ALTER TABLE %s ADD COLUMN city text DEFAULT 'changsha'" % table,
            "返回 ALTER TABLE", "ALTER TABLE", report_node="node135"),
    ]
    return case


def two_phase_target_column_case(case_id, name, section, resolver, start, gid,
                                 rollback_history, commit_rows, commit_history):
    """Specialize the 3.7 asymmetric-column lifecycle for another resolver."""
    case = deepcopy(two_phase_target_column_ignore_if_null_case())
    case.update({"id": case_id, "name": name, "section": section})
    end = start + 511
    replacements = {
        "ignore_if_null": resolver,
        "100001": str(start), "100512": str(end), "stream_100": gid,
    }
    for step in case["steps"]:
        for key in ("title", "display_sql", "expected"):
            if isinstance(step.get(key), str):
                step[key] = _replace_text(step[key], replacements)
        if isinstance(step.get("argv"), list):
            step["argv"] = [
                _replace_text(value, replacements) if isinstance(value, str) else value
                for value in step["argv"]
            ]
    case["conflict_configuration"][-1] = (
        "node134 表含 city 列，node135 缺 city；node135 的 target_column_missing=%s。" % resolver)
    for step in case["steps"]:
        if step["title"] == "将 node135 target_column_missing 设为 %s" % resolver:
            step["expected"] = "返回 node135,target_column_missing,%s" % resolver
            step["assertion"] = output_contains_text(
                "node135,target_column_missing,%s" % resolver)
        if step["title"].startswith("确认 rollback 后"):
            step["title"] = "确认 rollback 后无目标行且历史为 %s" % rollback_history
            step["expected"] = "45 秒内返回 0|%s" % rollback_history
            step["argv"][-1] = step["argv"][-1].replace("0|1", "0|%s" % rollback_history)
        if step["title"].startswith("确认 commit 后"):
            step["title"] = "确认 commit 后目标行和累计历史"
            step["expected"] = "45 秒内返回 %s|%s" % (commit_rows, commit_history)
            step["argv"][-1] = step["argv"][-1].replace("0|2", "%s|%s" % (commit_rows, commit_history))
    return case


def two_phase_source_column_default_case():
    """Build 3.8.1 by adapting the asymmetric 2PC lifecycle to source-missing."""
    case = deepcopy(two_phase_target_column_ignore_if_null_case())
    table = "immediate_parallel_source_column_2pc"
    replacements = {
        "immediate_parallel_target_column_2pc": table,
        "target_column_missing": "source_column_missing",
        "ignore_if_null": "use_default_value",
    }
    case.update({
        "id": "mmr.streaming_conflict.two_phase_source_column_missing_use_default_value",
        "name": "2PC streaming source_column_missing 的 use_default_value 策略",
        "section": "3.8.1",
    })
    for step in case["steps"]:
        for key in ("title", "display_sql", "expected"):
            if isinstance(step.get(key), str):
                step[key] = _replace_text(step[key], replacements)
        if isinstance(step.get("argv"), list):
            step["argv"] = [_replace_text(value, replacements) if isinstance(value, str) else value
                            for value in step["argv"]]
    case["conflict_configuration"][-1] = (
        "node134 表缺 city 列，node135 含默认 city；node135 的 source_column_missing=use_default_value。")
    for step in case["steps"]:
        if step["title"] == "将 node135 source_column_missing 设为 use_default_value":
            step["expected"] = "返回 node135,source_column_missing,use_default_value"
            step["assertion"] = output_contains_text(
                "node135,source_column_missing,use_default_value")
        if step["title"] == "按文档在 node134 创建含 city 的测试表":
            step["title"] = "按文档在 node134 创建缺 city 的测试表"
            step["display_sql"] = "CREATE TABLE %s(id int PRIMARY KEY,name name)" % table
            step["argv"][-1] = step["argv"][-1].replace(
                "CREATE TABLE %s(id int PRIMARY KEY,name name,city text DEFAULT 'changsha')" % table,
                step["display_sql"])
        elif step["title"] == "按文档在 node135 创建缺 city 的测试表":
            step["title"] = "按文档在 node135 创建含 city 的测试表"
            step["display_sql"] = "CREATE TABLE %s(id int PRIMARY KEY,name name,city text DEFAULT (chr(99)||chr(104)||chr(97)||chr(110)||chr(103)||chr(115)||chr(104)||chr(97)))" % table
            step["argv"][-1] = step["argv"][-1].replace(
                "CREATE TABLE %s(id int PRIMARY KEY,name name)" % table, step["display_sql"])
        elif step["title"] == "按文档恢复 node135 表结构":
            step["title"] = "按文档恢复 node134 表结构"
            step["display_sql"] = "ALTER TABLE %s ADD COLUMN city text DEFAULT (chr(99)||chr(104)||chr(97)||chr(110)||chr(103)||chr(115)||chr(104)||chr(97))" % table
            step["argv"][-1] = step["argv"][-1].replace("ALTER TABLE %s ADD COLUMN city text DEFAULT 'changsha'" % table, step["display_sql"])
            step["argv"] = [value.replace("-p %s" % PORT135, "-p %s" % PORT134)
                            if isinstance(value, str) else value for value in step["argv"]]
            step["report_node"] = "node134"
        elif "含 city 的 512 行插入" in step["title"]:
            step["title"] = step["title"].replace("含 city 的", "缺 city 的")
            step["display_sql"] = step["display_sql"].replace(
                "(id,name,city) SELECT gs,repeat('a',128),'changsha'",
                "(id,name) SELECT gs,repeat('a',128)")
            step["argv"][-1] = step["argv"][-1].replace(
                "(id,name,city) SELECT gs,repeat('a',128),'changsha'",
                "(id,name) SELECT gs,repeat('a',128)")
        if step["title"].startswith("确认 rollback 后"):
            step["title"] = "确认 rollback 后无目标行且无冲突历史"
            step["expected"] = "45 秒内返回 0|0"
            step["argv"][-1] = step["argv"][-1].replace("0|1", "0|0")
        if step["title"].startswith("确认 commit 后"):
            step["title"] = "确认 commit 后写入 512 行并记录 512 条历史"
            step["expected"] = "45 秒内返回 512|512"
            step["argv"][-1] = step["argv"][-1].replace("0|2", "512|512")
    return case


def two_phase_source_column_case(case_id, name, section, resolver, start, gid,
                                 rollback_history, commit_rows, commit_history):
    """Specialize the verified 3.8 source-column lifecycle."""
    case = deepcopy(two_phase_source_column_default_case())
    case.update({"id": case_id, "name": name, "section": section})
    end = start + 511
    replacements = {"use_default_value": resolver, "100001": str(start),
                    "100512": str(end), "stream_100": gid}
    for step in case["steps"]:
        for key in ("title", "display_sql", "expected"):
            if isinstance(step.get(key), str):
                step[key] = _replace_text(step[key], replacements)
        if isinstance(step.get("argv"), list):
            step["argv"] = [_replace_text(value, replacements) if isinstance(value, str) else value
                            for value in step["argv"]]
        if step["title"] == "将 node135 source_column_missing 设为 %s" % resolver:
            step["expected"] = "返回 node135,source_column_missing,%s" % resolver
            step["assertion"] = output_contains_text("node135,source_column_missing,%s" % resolver)
        if step["title"].startswith("确认 rollback 后"):
            step["expected"] = "45 秒内返回 0|%s" % rollback_history
            step["argv"][-1] = step["argv"][-1].replace("0|0", "0|%s" % rollback_history)
        if step["title"].startswith("确认 commit 后"):
            step["expected"] = "45 秒内返回 %s|%s" % (commit_rows, commit_history)
            step["argv"][-1] = step["argv"][-1].replace("512|512", "%s|%s" % (commit_rows, commit_history))
    return case


def two_phase_streaming_set_steps():
    """Keep only topology checks and set creation from the shared bootstrap.

    The non-2PC bootstrap also creates ``immediate_parallel_conflict``.  That
    table is irrelevant to the 2PC transfer-document flow and would obscure
    the SQL evidence in report.txt.
    """
    excluded_titles = {
        "按文档在 node134 创建测试表", "按文档在 node134 插入初始 100 行",
        "按文档确认 node134 初始数据为 100 行", "按文档在 node135 创建同构空表",
        "按文档在 node135 将表加入复制集", "按文档在所有节点订阅复制集",
        "按文档在 node135 异步执行复制集变更", "确认 node135 初始表为空",
    }
    return [step for step in common_streaming_set_steps()
            if step["title"] not in excluded_titles]


def two_phase_update_missing_case(case_id, name, section, resolver, missing_id, value,
                                  gid, rollback_history, commit_history,
                                  row_after_commit, history_pattern=None):
    """Build one isolated 3.2 resolver case from the document's shared flow."""
    history_pattern = history_pattern or resolver
    history_filter = "conflict_resolution LIKE '%s'" % history_pattern if "%" in history_pattern else "conflict_resolution='%s'" % history_pattern
    history_sql = ("SELECT count(*) FROM fdd.mmr_conflict_history WHERE "
                   "relname='%s' AND conflict_type='update_missing' AND %s" %
                   (TABLE, history_filter))
    target_row = ("SELECT (SELECT count(*) FROM %s WHERE id=%s%s)::text || '|' || "
                  "(%s)::text" %
                  (TABLE, missing_id,
                   " AND name=repeat('%s',128)" % value if row_after_commit else "",
                   history_sql))

    case = non2pc_case_base(case_id, name, section, [
        "node134=parallel,two_phase=false；node135=parallel,two_phase=true。",
        "node134=immediate、node135=buffered，logical_decoding_work_mem=64kB。",
        "node135 的 update_missing=%s；分别验证 rollback prepared 与 commit prepared。" % resolver,
    ])
    case["steps"] = two_phase_streaming_set_steps() + [
        sql("按文档在 node134 创建 2PC 测试表", PORT134,
            "CREATE TABLE %s(id int PRIMARY KEY,name text)" % TABLE,
            "返回 CREATE TABLE", "CREATE TABLE", report_node="node134"),
        sql("按文档在 node134 插入初始 100 行", PORT134,
            "INSERT INTO %s VALUES (generate_series(1,100),'a')" % TABLE,
            "返回 INSERT 0 100", "INSERT 0 100", report_node="node134"),
        sql("按文档在 node135 创建空 2PC 测试表", PORT135,
            "CREATE TABLE %s(id int PRIMARY KEY,name text)" % TABLE,
            "返回 CREATE TABLE", "CREATE TABLE", report_node="node135"),
        sql("按文档在 node135 将 2PC 表加入 set1", PORT135,
            "SELECT fdd.replication_set_add_table('%s'::regclass,'set1',false,false)" % TABLE,
            "复制集绑定成功", "replication_set_add_table", report_node="node135"),
        sql("按文档在所有节点订阅 set1", PORT134,
            "SELECT fdd.run_on_all_nodes('select fdd.alter_node_replication_sets(''{set1}'');')",
            "node134/node135 均成功", "node134", "node135", report_node="node134"),
        sql("按文档在 node135 异步执行复制集变更", PORT135,
            "SELECT fdd.replication_set_async_execute()", "异步生效",
            "replication_set_async_execute", report_node="node135"),
        sql("确认 node135 初始表为空", PORT135, "SELECT count(*) FROM %s" % TABLE,
            "返回 0", "0", report_node="node135"),
        sql("按文档插入 update_missing 前置 512 行", PORT134,
            "INSERT INTO %s(id,name) SELECT gs,repeat('a',128) FROM generate_series(90001,90512) gs" % TABLE,
            "返回 INSERT 0 512", "INSERT 0 512", report_node="node134"),
        wait_for_rows("等待 node135 收到 update_missing 前置 512 行", PORT135,
                      "SELECT count(*) FROM %s WHERE id BETWEEN 90001 AND 90512" % TABLE,
                      "512", "node135"),
        sql("将 node135 update_missing 设为 %s" % resolver, PORT135,
            "SELECT fdd.alter_local_node_set_conflict_resolver('update_missing','%s')" % resolver,
            "返回 node135,update_missing,%s" % resolver,
            "node135,update_missing,%s" % resolver, report_node="node135"),
        sql("确认触发前 %s 冲突记录为 0" % resolver, PORT135, history_sql,
            "返回 0", "0", report_node="node135"),
        sql("按文档准备第一次 update_missing 事务", PORT134,
            "BEGIN; UPDATE %s SET name=repeat('%s',128) WHERE id=%s OR id BETWEEN 90001 AND 90512; PREPARE TRANSACTION '%s'" %
            (TABLE, value, missing_id, gid),
            "返回 BEGIN、UPDATE 513、PREPARE TRANSACTION", "BEGIN", "UPDATE 513",
            "PREPARE TRANSACTION", report_node="node134"),
        sql("按文档回滚第一次预备事务", PORT134, "ROLLBACK PREPARED '%s'" % gid,
            "返回 ROLLBACK PREPARED", "ROLLBACK PREPARED", report_node="node134"),
        wait_for_rows("确认 rollback 后缺失行未写入且历史为 %s" % rollback_history,
                      PORT135,
                      "SELECT (SELECT count(*) FROM %s WHERE id=%s)::text || '|' || (%s)::text" %
                      (TABLE, missing_id, history_sql),
                      "0|%s" % rollback_history, "node135"),
        sql("按文档准备第二次 update_missing 事务", PORT134,
            "BEGIN; UPDATE %s SET name=repeat('%s',128) WHERE id=%s OR id BETWEEN 90001 AND 90512; PREPARE TRANSACTION '%s'" %
            (TABLE, value, missing_id, gid),
            "返回 BEGIN、UPDATE 513、PREPARE TRANSACTION", "BEGIN", "UPDATE 513",
            "PREPARE TRANSACTION", report_node="node134"),
        sql("按文档提交第二次预备事务", PORT134, "COMMIT PREPARED '%s'" % gid,
            "返回 COMMIT PREPARED", "COMMIT PREPARED", report_node="node134"),
        wait_for_rows("确认 commit 后缺失行处理结果和累计冲突历史", PORT135,
                      target_row, "%s|%s" % (1 if row_after_commit else 0, commit_history),
                      "node135"),
    ]
    return case


def two_phase_insert_exists_case(case_id, name, section, resolver, conflict_id,
                                 range_start, value, gid, rollback_history,
                                 commit_history, update_conflict_row,
                                 history_pattern=None, commit_range_rows=512):
    """Build one isolated 3.3 resolver case from the documented reverse setup."""
    table = "immediate_parallel_insert_exists"
    history_pattern = history_pattern or resolver
    history_filter = "conflict_resolution LIKE '%s'" % history_pattern if "%" in history_pattern else "conflict_resolution='%s'" % history_pattern
    history_sql = ("SELECT count(*) FROM fdd.mmr_conflict_history WHERE relname='%s' "
                   "AND conflict_type='insert_exists' AND %s" % (table, history_filter))
    range_end = range_start + 511
    insert_sql = ("INSERT INTO %s(id,name) SELECT gs,repeat('%s',128) FROM "
                  "generate_series(%s,%s) gs WHERE gs=%s OR gs BETWEEN %s AND %s" %
                  (table, value, conflict_id, range_end, conflict_id, range_start, range_end))
    case = non2pc_case_base(case_id, name, section, [
        "node134=parallel,two_phase=false；node135=parallel,two_phase=true。",
        "node134=immediate、node135=buffered，logical_decoding_work_mem=64kB。",
        "node135 的 insert_exists=%s；分别验证 rollback prepared 与 commit prepared。" % resolver,
    ])
    case["steps"] = two_phase_streaming_set_steps() + [
        sql("按文档在 node134 创建 insert_exists 测试表", PORT134,
            "CREATE TABLE %s(id int PRIMARY KEY,name text)" % table,
            "返回 CREATE TABLE", "CREATE TABLE", report_node="node134"),
        sql("按文档在 node135 创建表并插入初始 100 行", PORT135,
            "CREATE TABLE %s(id int PRIMARY KEY,name text); INSERT INTO %s VALUES (generate_series(1,100),'a')" % (table, table),
            "返回 CREATE TABLE、INSERT 0 100", "CREATE TABLE", "INSERT 0 100", report_node="node135"),
        sql("按文档在 node135 将表加入 set1", PORT135,
            "SELECT fdd.replication_set_add_table('%s'::regclass,'set1',false,false)" % table,
            "复制集绑定成功", "replication_set_add_table", report_node="node135"),
        sql("按文档在所有节点订阅 set1", PORT134,
            "SELECT fdd.run_on_all_nodes('select fdd.alter_node_replication_sets(''{set1}'');')",
            "node134/node135 均成功", "node134", "node135", report_node="node134"),
        sql("按文档在 node135 异步执行复制集变更", PORT135,
            "SELECT fdd.replication_set_async_execute()", "异步生效", "replication_set_async_execute", report_node="node135"),
        sql("确认 node135 有 100 行初始数据", PORT135, "SELECT count(*) FROM %s" % table,
            "返回 100", "100", report_node="node135"),
        sql("确认 node134 初始表为空", PORT134, "SELECT count(*) FROM %s" % table,
            "返回 0", "0", report_node="node134"),
        sql("将 node135 insert_exists 设为 %s" % resolver, PORT135,
            "SELECT fdd.alter_local_node_set_conflict_resolver('insert_exists','%s')" % resolver,
            "返回 node135,insert_exists,%s" % resolver,
            "node135,insert_exists,%s" % resolver, report_node="node135"),
        sql("确认触发前 %s 冲突记录为 0" % resolver, PORT135, history_sql,
            "返回 0", "0", report_node="node135"),
        sql("按文档准备第一次 insert_exists 事务", PORT134,
            "BEGIN; %s; PREPARE TRANSACTION '%s'" % (insert_sql, gid),
            "返回 BEGIN、INSERT 0 513、PREPARE TRANSACTION", "BEGIN", "INSERT 0 513",
            "PREPARE TRANSACTION", report_node="node134"),
        sql("按文档回滚第一次预备事务", PORT134, "ROLLBACK PREPARED '%s'" % gid,
            "返回 ROLLBACK PREPARED", "ROLLBACK PREPARED", report_node="node134"),
        wait_for_rows("确认 rollback 后 512 行未写入、冲突行仍为 a 且历史为 %s" % rollback_history,
                      PORT135, "SELECT (SELECT count(*) FROM %s WHERE id BETWEEN %s AND %s)::text || '|' || "
                      "(SELECT count(*) FROM %s WHERE id=%s AND name='a')::text || '|' || (%s)::text" %
                      (table, range_start, range_end, table, conflict_id, history_sql),
                      "0|1|%s" % rollback_history, "node135"),
        sql("按文档准备第二次 insert_exists 事务", PORT134,
            "BEGIN; %s; PREPARE TRANSACTION '%s'" % (insert_sql, gid),
            "返回 BEGIN、INSERT 0 513、PREPARE TRANSACTION", "BEGIN", "INSERT 0 513",
            "PREPARE TRANSACTION", report_node="node134"),
        sql("按文档提交第二次预备事务", PORT134, "COMMIT PREPARED '%s'" % gid,
            "返回 COMMIT PREPARED", "COMMIT PREPARED", report_node="node134"),
        wait_for_rows("确认 commit 后 512 行、冲突行和累计冲突历史", PORT135,
                      "SELECT (SELECT count(*) FROM %s WHERE id BETWEEN %s AND %s)::text || '|' || "
                      "(SELECT count(*) FROM %s WHERE id=%s AND name=%s)::text || '|' || (%s)::text" %
                      (table, range_start, range_end, table, conflict_id,
                       "repeat('%s',128)" % value if update_conflict_row else "'a'", history_sql),
                      "%s|1|%s" % (commit_range_rows, commit_history), "node135"),
    ]
    return case
