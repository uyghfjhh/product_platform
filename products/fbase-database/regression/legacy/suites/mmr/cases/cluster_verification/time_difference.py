from framework.assertions import command_succeeds, output_contains_text, rows_equal
from framework.steps import command_step, sql_step, wait_sql_step


SOURCE = "mmr:mmr1"
TARGET = "mmr:mmr2"
TARGET_DATA = "/home/postgres/pgdata/mmr2"
PGCTL = "/usr/local/fbase15.15/bin/pg_ctl"


def time_diff_present(expected):
    return sql_step(
        "确认时间差校验结果", "postgres",
        "SELECT (count(*) FILTER (WHERE is_abnormal='TIME_DIFF') > 0)::text "
        "FROM fdd.show_node_info(true,false)",
        "返回 %s" % expected, rows_equal([[expected]]), node=SOURCE)


CASE = {
    "id": "mmr.cluster_verification.time_difference",
    "name": "多活集群校验节点时间差及最低优先级",
    "document": "多活功能测试文档.md",
    "section": "5.1.4",
    "group": "cluster_verification",
    "fixtures": [
        "cluster",
        {"type": "settings", "node": SOURCE, "apply": "reload",
         "values": {"fdd.time_diff": "1ms", "fdd.search_dead_tup_time_interval": "1ms"},
         "purpose": "按文档将时间差阈值设为 1ms"},
        {"type": "faketime_node_guard", "node": TARGET},
        {"type": "mmr_node_source_guard", "node": SOURCE, "node_id": 2},
    ],
    "requirements": {
        "clusters": ["mmr"], "plugins": ["fdd_mmr"], "groups": ["mmr"],
        "writable_node": True, "node": SOURCE, "commands": ["faketime"],
    },
    "evidence_nodes": [SOURCE, TARGET],
    "prerequisites": [
        "隔离两成员集群 healthy；mmr2 使用 faketime 单独偏移 5 秒。",
        "faketime_node_guard 无条件以真实主机时钟重启 mmr2；source_node_id guard 恢复文档制造的元数据不一致。",
    ],
    "steps": [
        sql_step("确认文档时间阈值参数为 1ms", "postgres",
                 "SHOW fdd.time_diff; SHOW fdd.search_dead_tup_time_interval",
                 "分别返回 1ms 和 1ms", output_contains_text("1ms"), node=SOURCE),
        {"type": "node_action", "title": "停止 mmr2 以切换进程时钟", "node": TARGET,
         "action": "stop", "expected": "停止成功", "assertion": command_succeeds()},
        command_step("以快 5 秒的 faketime 后台启动 mmr2",
                     ["sh", "-c", "faketime '+5 sec' %s -D %s -l %s "
                      "start >%s 2>&1 &" %
                      (PGCTL, TARGET_DATA, TARGET_DATA + "/faketime-start.log",
                       TARGET_DATA + "/faketime-pgctl.log")],
                     "后台启动命令返回 0", command_succeeds(), node=TARGET, timeout=10),
        wait_sql_step("确认带偏移时钟的 mmr2 已接受连接", "postgres",
                      "SELECT clock_timestamp()", "SQL 执行成功", command_succeeds(),
                      node=TARGET, timeout=20),
        sql_step("展示超过最小阈值的 TIME_DIFF", "postgres",
                 "SELECT nodeid,is_abnormal,detail FROM fdd.show_node_info(true,false) "
                 "WHERE is_abnormal='TIME_DIFF'",
                 "显示 TIME_DIFF 和超过本节点死行查询时间",
                 output_contains_text("TIME_DIFF", "超过本节点死行查询时间"), node=SOURCE),
        time_diff_present("true"),
        sql_step("按文档禁用时间差校验", "postgres",
                 "ALTER SYSTEM SET fdd.time_diff='-1'",
                 "ALTER SYSTEM 成功", command_succeeds(), node=SOURCE),
        sql_step("重载禁用后的时间差配置", "postgres", "SELECT pg_reload_conf()",
                 "返回 t", rows_equal([["t"]]), node=SOURCE),
        sql_step("确认禁用后不再报告 TIME_DIFF", "postgres",
                 "SELECT count(*) FILTER (WHERE is_abnormal='TIME_DIFF')::text "
                 "FROM fdd.show_node_info(true,false)",
                 "返回 0", rows_equal([["0"]]), node=SOURCE),
        sql_step("按文档重新启用 1ms 时间差校验", "postgres",
                 "ALTER SYSTEM SET fdd.time_diff='1ms'",
                 "ALTER SYSTEM 成功", command_succeeds(), node=SOURCE),
        sql_step("重载重新启用后的时间差配置", "postgres", "SELECT pg_reload_conf()",
                 "返回 t", rows_equal([["t"]]), node=SOURCE),
        time_diff_present("true"),
        sql_step("在 mmr1 制造 node2 的元数据不一致", "postgres",
                 "UPDATE fdd.mmr_node SET source_node_id=2 WHERE node_id=2",
                 "UPDATE 成功", command_succeeds(), node=SOURCE),
        sql_step("确认元数据差异优先于时间差校验", "postgres",
                 "WITH result AS (SELECT is_abnormal FROM fdd.show_node_info(true,false)) "
                 "SELECT (count(*) FILTER (WHERE is_abnormal='DIFF_ClUSTER') > 0)::text,"
                 "(count(*) FILTER (WHERE is_abnormal='TIME_DIFF') = 0)::text FROM result",
                 "返回 true|true：元数据差异优先，时间差校验被抑制",
                 rows_equal([["true", "true"]]), node=SOURCE),
        sql_step("恢复 mmr1 的 node2 元数据", "postgres",
                 "UPDATE fdd.mmr_node SET source_node_id=1 WHERE node_id=2",
                 "UPDATE 成功", command_succeeds(), node=SOURCE),
        {"type": "node_action", "title": "停止带偏移时钟的 mmr2", "node": TARGET,
         "action": "stop", "expected": "停止成功", "assertion": command_succeeds()},
        {"type": "node_action", "title": "以真实主机时钟启动 mmr2", "node": TARGET,
         "action": "start", "expected": "启动成功", "assertion": command_succeeds()},
        command_step("等待逻辑复制 worker 以真实时钟完成重连", ["sleep", "10"],
                     "等待命令返回 0", command_succeeds(), node=SOURCE, timeout=15),
        wait_sql_step("确认 MMR 订阅 worker 已恢复", "postgres",
                      "SELECT (count(*) >= 1)::text FROM pg_subscription s JOIN pg_stat_subscription st "
                      "ON st.subid=s.oid WHERE s.subname LIKE 'fmmr_%%' AND st.pid IS NOT NULL",
                      "返回 true", rows_equal([["true"]]), node=SOURCE, timeout=20),
        wait_sql_step("确认清理后多活集群恢复健康", "postgres",
                      "SELECT (count(*) >= 2)::text || '|' || bool_and(is_abnormal='OK')::text "
                      "FROM fdd.show_node_info(true,false)",
                      "返回 true|true", rows_equal([["true|true"]]), node=SOURCE, timeout=20),
    ],
    "teardown": "fixture 恢复 fdd.time_diff、fdd.search_dead_tup_time_interval 和 source_node_id；"
                "无论执行结果如何均以真实时钟重启 mmr2。",
}
