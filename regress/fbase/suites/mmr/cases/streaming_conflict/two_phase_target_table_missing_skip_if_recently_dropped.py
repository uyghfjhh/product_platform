"""Streaming conflict document 3.9.1: 2PC target_table_missing default resolver."""

from suites.mmr.cases.streaming_conflict.delete_missing_skip import PORT134, PORT135, sql, wait_for_rows
from suites.mmr.cases.streaming_conflict.update_missing_insert_or_skip import common_streaming_set_steps
from copy import deepcopy

from framework.assertions import output_contains_text
from suites.mmr.streaming_conflict_support import non2pc_case_base

TABLE = "immediate_parallel_drop_2pc"
HISTORY = ("SELECT count(*) FROM fdd.mmr_conflict_history WHERE relname='%s' "
           "AND conflict_type='target_table_missing' AND conflict_resolution='skip_if_recently_dropped'" % TABLE)

CASE = non2pc_case_base(
    "mmr.streaming_conflict.two_phase_target_table_missing_skip_if_recently_dropped",
    "2PC streaming target_table_missing 的 skip_if_recently_dropped 策略", "3.9.1", [
        "node134=parallel,two_phase=false；node135=parallel,two_phase=true。",
        "node134=immediate、node135=buffered，logical_decoding_work_mem=64kB。",
        "node135 删除已订阅表；target_table_missing=skip_if_recently_dropped。",
    ])
CASE["steps"] = common_streaming_set_steps()[:14] + [
    sql("按文档在所有节点订阅默认复制集 g1", PORT134,
        "SELECT fdd.run_on_all_nodes('select fdd.alter_node_replication_sets(''{g1}'');')",
        "node134/node135 均成功", "node134", "node135", report_node="node134"),
    sql("按文档在所有节点创建目标缺失测试表", PORT134,
        "SELECT fdd.run_on_all_nodes('CREATE TABLE %s(id int PRIMARY KEY,name text)')" % TABLE,
        "node134/node135 均返回 CREATE TABLE", "node134", "node135", "CREATE TABLE", report_node="node134"),
    sql("按文档在 node135 异步执行复制集变更", PORT135,
        "SELECT fdd.replication_set_async_execute()", "异步生效", "replication_set_async_execute", report_node="node135"),
    sql("按文档在 node135 删除测试表", PORT135, "DROP TABLE %s" % TABLE,
        "返回 DROP TABLE", "DROP TABLE", report_node="node135"),
    sql("将 node135 target_table_missing 设为 skip_if_recently_dropped", PORT135,
        "SELECT fdd.alter_local_node_set_conflict_resolver('target_table_missing','skip_if_recently_dropped')",
        "返回 node135,target_table_missing,skip_if_recently_dropped", "node135,target_table_missing,skip_if_recently_dropped", report_node="node135"),
    sql("确认触发前冲突记录为 0", PORT135, HISTORY, "返回 0", "0", report_node="node135"),
    sql("按文档准备第一次 512 行插入", PORT134,
        "BEGIN; INSERT INTO %s(id,name) SELECT gs,repeat('a',128) FROM generate_series(100001,100512) gs; PREPARE TRANSACTION 'stream_100'" % TABLE,
        "返回 BEGIN、INSERT 0 512、PREPARE TRANSACTION", "BEGIN", "INSERT 0 512", "PREPARE TRANSACTION", report_node="node134"),
    sql("按文档回滚第一次预备事务", PORT134, "ROLLBACK PREPARED 'stream_100'", "返回 ROLLBACK PREPARED", "ROLLBACK PREPARED", report_node="node134"),
    wait_for_rows("确认 rollback 后无冲突历史", PORT135, HISTORY, "0", "node135"),
    sql("按文档准备第二次 512 行插入", PORT134,
        "BEGIN; INSERT INTO %s(id,name) SELECT gs,repeat('a',128) FROM generate_series(100001,100512) gs; PREPARE TRANSACTION 'stream_100'" % TABLE,
        "返回 BEGIN、INSERT 0 512、PREPARE TRANSACTION", "BEGIN", "INSERT 0 512", "PREPARE TRANSACTION", report_node="node134"),
    sql("按文档提交第二次预备事务", PORT134, "COMMIT PREPARED 'stream_100'", "返回 COMMIT PREPARED", "COMMIT PREPARED", report_node="node134"),
    wait_for_rows("确认 commit 后记录 512 条 skip_if_recently_dropped 历史", PORT135, HISTORY, "512", "node135"),
]


def build_target_table_case(case_id, name, section, resolver, start, gid,
                            rollback_history, commit_history, pattern=False):
    case = deepcopy(CASE)
    case.update({"id": case_id, "name": name, "section": section})
    end = start + 511
    replacements = {"skip_if_recently_dropped": resolver, "100001": str(start),
                    "100512": str(end), "stream_100": gid}
    for step in case["steps"]:
        for key in ("title", "display_sql", "expected"):
            if isinstance(step.get(key), str):
                for old, new in replacements.items():
                    step[key] = step[key].replace(old, new)
        if isinstance(step.get("argv"), list):
            step["argv"] = [
                value if not isinstance(value, str) else _replace(value, replacements)
                for value in step["argv"]]
        if step["title"] == "将 node135 target_table_missing 设为 %s" % resolver:
            step["expected"] = "返回 node135,target_table_missing,%s" % resolver
            step["assertion"] = output_contains_text("node135,target_table_missing,%s" % resolver)
        if pattern:
            for key in ("display_sql",):
                if isinstance(step.get(key), str):
                    step[key] = step[key].replace("conflict_resolution='%s'" % resolver,
                                                    "conflict_resolution LIKE '%s%%'" % resolver)
            if isinstance(step.get("argv"), list):
                step["argv"] = [value.replace("conflict_resolution='%s'" % resolver,
                                                "conflict_resolution LIKE '%s%%'" % resolver)
                                if isinstance(value, str) else value for value in step["argv"]]
        if step["title"].startswith("确认 rollback 后"):
            step["expected"] = "45 秒内返回 %s" % rollback_history
            step["argv"][-1] = step["argv"][-1].replace("'0'", "'%s'" % rollback_history)
        if step["title"].startswith("确认 commit 后"):
            step["expected"] = "45 秒内返回 %s" % commit_history
            step["argv"][-1] = step["argv"][-1].replace("'512'", "'%s'" % commit_history)
    return case


def _replace(value, replacements):
    for old, new in replacements.items():
        value = value.replace(old, new)
    return value
