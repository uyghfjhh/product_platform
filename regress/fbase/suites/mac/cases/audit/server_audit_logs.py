from framework.assertions import command_succeeds, rows_equal
from framework.steps import wait_sql_step


CASE = {
    "id": "mac.audit.server_audit_logs",
    "name": "服务器级审计日志记录",
    "document": "审计功能转测（邹雪、陈群友）.md",
    "section": "5.4.2",
    "group": "audit",
    "fixtures": [
        "cluster",
        {"type": "settings", "user": "sao", "values": {"fdb.enable_audit": "1"},
         "apply": "reload", "restore_runtime_value": True,
         "purpose": "启用服务器级审计日志所需的审计开关"},
    ],
    "requirements": {"plugins": ["fbase_mac"], "writable_node": True, "node": "primary", "roles": ["sao"]},
    "prerequisites": ["审计功能开启，SAO 可以查看 fdb_audit.audit_records"],
    "steps": [
        {"type": "cluster_action", "title": "触发服务器配置重载", "action": "reload",
         "expected": "reload 执行成功", "assertion": command_succeeds()},
        wait_sql_step("SAO 查到本轮配置重载的服务器级审计日志", "sao",
                      "SELECT EXISTS (SELECT 1 FROM fdb_audit.audit_records "
                      "WHERE username IS NULL AND sql = 'received SIGHUP and reloading configuration files' "
                      "AND time::timestamptz > now() - interval '30 seconds')",
                      "返回 true，说明审计开关开启后自动记录服务器配置重载事件",
                      rows_equal([["t"]]), timeout=15, interval=1),
    ],
    "teardown": "settings fixture 恢复审计开关。",
}
