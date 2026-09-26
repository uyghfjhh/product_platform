from framework.assertions import command_succeeds, rows_equal
from framework.steps import sql_step


CASE = {
    "id": "mac.audit.enable_audit",
    "name": "SAO 开启审计功能",
    "document": "审计功能转测（邹雪、陈群友）.md",
    "section": "5.1",
    "group": "audit",
    "fixtures": [
        "cluster",
        {"type": "settings", "user": "sao", "setup": False,
         "values": {"fdb.enable_audit": "1"}, "apply": "reload",
         "restore_runtime_value": True,
         "purpose": "恢复审计开关的用例前状态"},
    ],
    "requirements": {"plugins": ["fbase_mac"], "writable_node": True, "node": "primary",
                     "roles": ["sao"]},
    "prerequisites": [
        "mac 集群已由平台创建并处于运行状态",
        "SAO 有权设置并重载 fdb.enable_audit",
    ],
    "steps": [
        sql_step("SAO 开启审计功能", "sao", "ALTER SYSTEM SET fdb.enable_audit = 1",
                 "返回 ALTER SYSTEM", command_succeeds()),
        {"type": "cluster_action", "title": "重载审计配置", "action": "reload",
         "expected": "reload 执行成功", "assertion": command_succeeds()},
        sql_step("确认审计功能已开启", "sao", "SHOW fdb.enable_audit", "返回 1",
                 rows_equal([["1"]])),
    ],
    "teardown": "settings fixture 恢复 fdb.enable_audit 并 reload。",
}
