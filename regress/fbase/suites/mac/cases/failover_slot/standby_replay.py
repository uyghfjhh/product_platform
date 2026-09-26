from framework.assertions import command_succeeds, rows_equal
from framework.steps import sql_step, wait_sql_step


SLOT = "fbase_regress_failover_replay"


CASE = {
    "id": "mac.failover_slot.standby_replay",
    "name": "故障转移槽主备创建和删除重放",
    "document": "故障转移槽转测文档.md",
    "section": "3.1,3.2,3.3",
    "group": "failover_slot",
    "fixtures": ["cluster", {"type": "settings", "setup": False, "restore_runtime_value": True,
                  "values": {"synchronous_standby_names": "*"}, "apply": "reload",
                  "purpose": "恢复故障转移槽 WAL 同步所需的同步备库配置"},
                 {"type": "logical_slot", "name": SLOT}],
    "requirements": {"writable_node": True, "node": "primary", "groups": ["streaming"],
                     "nodes": ["standby"]},
    "prerequisites": ["主备流复制健康；用例临时配置同步备库，专属 failover 槽在异常路径由 fixture 删除"],
    "steps": [
        sql_step("配置同步备库", "postgres", "ALTER SYSTEM SET synchronous_standby_names = '*'",
                 "返回 ALTER SYSTEM", command_succeeds()),
        sql_step("重载同步复制配置", "postgres", "SELECT pg_reload_conf()", "返回 true", rows_equal([["t"]])),
        sql_step("主库创建故障转移逻辑复制槽", "postgres",
                 "SELECT slot_name FROM pg_create_logical_replication_slot('%s','pgoutput',false,false,true)" % SLOT,
                 "返回专属槽名", rows_equal([[SLOT]])),
        wait_sql_step("备库重放后出现同名 failover 槽", "postgres",
                      "SELECT slot_name, failover::text FROM pg_get_replication_slots() WHERE slot_name = '%s'" % SLOT,
                      "返回专属槽名|true", rows_equal([[SLOT, "true"]]),
                      node="standby", timeout=20, interval=1),
        sql_step("主库删除故障转移逻辑复制槽", "postgres",
                 "SELECT pg_drop_replication_slot('%s')" % SLOT, "删除成功", command_succeeds()),
        wait_sql_step("备库重放后删除同名槽", "postgres",
                      "SELECT count(*)::text FROM pg_get_replication_slots() WHERE slot_name = '%s'" % SLOT,
                      "返回 0", rows_equal([["0"]]), node="standby", timeout=20, interval=1),
    ],
    "teardown": "logical_slot fixture 删除主库遗留槽；settings fixture 恢复 synchronous_standby_names 并 reload。",
}
