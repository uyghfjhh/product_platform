"""Streaming-conflict document section 1 configuration combinations."""

from suites.mmr.cases.streaming_conflict.delete_missing_skip import (
    PORT134, PORT135, sql, wait_for_rows,
)
from suites.mmr.cases.streaming_conflict.update_missing_insert_or_skip import (
    common_streaming_set_steps,
)
from suites.mmr.streaming_conflict_support import non2pc_case_base


MODES = (
    ("immediate", "immediate", "ii", 110001),
    ("immediate", "buffered", "ib", 120001),
    ("buffered", "immediate", "bi", 130001),
    ("buffered", "buffered", "bb", 140001),
)


def mode_steps(source_mode, target_mode, suffix, start):
    """Run document 2.1.1's large DELETE under one GUC combination."""
    end = start + 511
    table = "stream_conf_mode_%s" % suffix
    return [
        sql("设置 node134 为 %s" % source_mode, PORT134,
            "ALTER SYSTEM SET debug_logical_replication_streaming='%s'" % source_mode,
            "返回 ALTER SYSTEM", "ALTER SYSTEM", report_node="node134"),
        sql("重新加载 node134 %s 配置" % source_mode, PORT134,
            "SELECT pg_reload_conf()", "返回 true", "t", report_node="node134"),
        sql("设置 node135 为 %s" % target_mode, PORT135,
            "ALTER SYSTEM SET debug_logical_replication_streaming='%s'" % target_mode,
            "返回 ALTER SYSTEM", "ALTER SYSTEM", report_node="node135"),
        sql("重新加载 node135 %s 配置" % target_mode, PORT135,
            "SELECT pg_reload_conf()", "返回 true", "t", report_node="node135"),
        sql("确认 %s 组合的发布端参数" % suffix, PORT134,
            "SHOW debug_logical_replication_streaming", "返回 %s" % source_mode,
            source_mode, report_node="node134"),
        sql("确认 %s 组合的订阅端参数" % suffix, PORT135,
            "SHOW debug_logical_replication_streaming", "返回 %s" % target_mode,
            target_mode, report_node="node135"),
        sql("创建 %s 组合的源端表和目标端缺失行" % suffix, PORT134,
            "CREATE TABLE %s(id int PRIMARY KEY,name text); INSERT INTO %s VALUES(100,'initial-missing')" %
            (table, table),
            "返回 CREATE TABLE、INSERT 0 1", "CREATE TABLE", "INSERT 0 1",
            report_node="node134"),
        sql("创建 %s 组合的同构空目标表" % suffix, PORT135,
            "CREATE TABLE %s(id int PRIMARY KEY,name text)" % table,
            "返回 CREATE TABLE", "CREATE TABLE", report_node="node135"),
        sql("将 %s 表加入 set1" % suffix, PORT135,
            "SELECT fdd.replication_set_add_table('%s'::regclass,'set1',false,false)" % table,
            "复制集绑定成功", "replication_set_add_table", report_node="node135"),
        sql("在所有节点订阅 %s 表" % suffix, PORT134,
            "SELECT fdd.run_on_all_nodes('select fdd.alter_node_replication_sets(''{set1}'');')",
            "node134/node135 均成功", "node134", "node135", report_node="node134"),
        sql("在 node135 异步生效 %s 表" % suffix, PORT135,
            "SELECT fdd.replication_set_async_execute()", "异步生效",
            "replication_set_async_execute", report_node="node135"),
        sql("将 %s 组合的 delete_missing 设为 skip" % suffix, PORT135,
            "SELECT fdd.alter_local_node_set_conflict_resolver('delete_missing','skip')",
            "返回 node135,delete_missing,skip", "node135,delete_missing,skip",
            report_node="node135"),
        sql("插入 %s 组合的 512 行 streaming 前置数据" % suffix, PORT134,
            "INSERT INTO %s(id,name) SELECT gs,repeat('a',128) FROM generate_series(%s,%s) gs" %
            (table, start, end),
            "返回 INSERT 0 512", "INSERT 0 512", report_node="node134"),
        wait_for_rows("等待 node135 收到 %s 组合的 512 行数据" % suffix, PORT135,
                      "SELECT count(*) FROM %s WHERE id BETWEEN %s AND %s" %
                      (table, start, end), "512", "node135"),
        sql("按文档删除 %s 组合的缺失行和 512 行数据" % suffix, PORT134,
            "DELETE FROM %s WHERE id=100 OR id BETWEEN %s AND %s" % (table, start, end),
            "返回 DELETE 513", "DELETE 513", report_node="node134"),
        wait_for_rows("确认 %s 组合保留 skip 结果和一条 delete_missing 历史" % suffix, PORT135,
                      "SELECT (SELECT count(*) FROM %s WHERE id=100 OR id BETWEEN %s AND %s)::text || '|' || (SELECT count(*) FROM fdd.mmr_conflict_history WHERE relname='%s' AND conflict_type='delete_missing' AND conflict_resolution='skip')::text" %
                      (table, start, end, table), "0|1", "node135"),
    ]


CASE = non2pc_case_base(
    "mmr.streaming_conflict.configuration_matrix",
    "streaming 冲突的发布端与订阅端 GUC 组合", "1",
    [
        "按文档分别覆盖发布端/订阅端 immediate 与 buffered 的四种组合。",
        "两端 MMR streaming=parallel；node134 two_phase=false，node135 two_phase=true；logical_decoding_work_mem=64kB。",
        "每种组合执行 2.1.1 同类的 513 行 DELETE，验证大事务 streaming 与 delete_missing/skip 不因 GUC 组合改变结果。",
    ])

CASE["steps"] = common_streaming_set_steps()
for _source_mode, _target_mode, _suffix, _start in MODES:
    CASE["steps"].extend(mode_steps(_source_mode, _target_mode, _suffix, _start))
