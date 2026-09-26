from framework.assertions import command_succeeds, rows_equal, sql_fails
from framework.steps import sql_step


SLOT = "fbase_regress_failover_slot"


CASE = {
    "id": "mac.failover_slot.create_view_drop",
    "name": "故障转移逻辑复制槽创建、查看和删除",
    "document": "故障转移槽转测文档.md",
    "section": "2.2,2.3,2.4,3.1,3.2",
    "group": "failover_slot",
    "fixtures": ["cluster", {"type": "settings", "setup": False, "restore_runtime_value": True,
                  "values": {"synchronous_standby_names": "*"}, "apply": "reload",
                  "purpose": "恢复故障转移槽所需的同步备库配置"},
                 {"type": "logical_slot", "name": SLOT}],
    "requirements": {"writable_node": True, "node": "primary", "groups": ["streaming"]},
    "prerequisites": ["主备流复制健康；故障转移槽要求同步备库，测试结束删除专属复制槽并恢复同步配置"],
    "steps": [
        sql_step("配置同步备库以满足 failover 槽前置条件", "postgres",
                 "ALTER SYSTEM SET synchronous_standby_names = '*'", "返回 ALTER SYSTEM", command_succeeds()),
        sql_step("重载同步复制配置", "postgres", "SELECT pg_reload_conf()", "返回 true", rows_equal([["t"]])),
        sql_step("确认备库处于同步复制状态", "postgres",
                 "SELECT sync_state FROM pg_stat_replication WHERE state = 'streaming'", "返回 sync", rows_equal([["sync"]])),
        sql_step("临时逻辑槽不能设置 failover", "postgres",
                 "SELECT pg_create_logical_replication_slot('fbase_regress_temp_failover', 'pgoutput', true, false, true)",
                 "执行失败，临时复制槽不能为故障转移槽", sql_fails("temporary replication slots cannot be failover slots")),
        sql_step("创建持久故障转移逻辑复制槽", "postgres",
                 "SELECT slot_name FROM pg_create_logical_replication_slot('%s', 'pgoutput', false, false, true)" % SLOT,
                 "返回专属槽名", rows_equal([[SLOT]])),
        sql_step("查看槽的 failover 标记和逻辑槽属性", "postgres",
                 "SELECT slot_name, plugin, slot_type, temporary::text, two_phase::text, failover::text FROM pg_get_replication_slots() WHERE slot_name = '%s'" % SLOT,
                 "返回槽名|pgoutput|logical|false|false|true", rows_equal([[SLOT, "pgoutput", "logical", "false", "false", "true"]])),
        sql_step("删除故障转移逻辑复制槽", "postgres", "SELECT pg_drop_replication_slot('%s')" % SLOT,
                 "删除成功", command_succeeds()),
        sql_step("确认故障转移槽已从视图消失", "postgres",
                 "SELECT count(*)::text FROM pg_get_replication_slots() WHERE slot_name = '%s'" % SLOT,
                 "返回 0", rows_equal([["0"]])),
    ],
    "teardown": "logical_slot fixture 在异常路径删除专属槽；settings fixture 恢复 synchronous_standby_names 并 reload。",
}
