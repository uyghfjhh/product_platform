from framework.assertions import command_succeeds, rows_equal
from framework.steps import sql_step


def _set_and_reload(title, name, value, expected):
    return [
        sql_step(title, "sao", "ALTER SYSTEM SET %s = '%s'" % (name, value),
                 "返回 ALTER SYSTEM", command_succeeds()),
        {"type": "cluster_action", "title": "重载 %s 配置" % name, "action": "reload",
         "expected": "reload 执行成功", "assertion": command_succeeds()},
        sql_step("确认 %s 已生效" % name, "sao", "SHOW %s" % name, expected,
                 rows_equal([[value]])),
    ]


STEPS = []
STEPS += _set_and_reload("SAO 修改审计日志保存目录", "fdb.audit_log_directory",
                         "fbase_regress_audit", "返回 fbase_regress_audit")
STEPS.append(sql_step("确认新审计目录的当前日志可读取", "sao",
                      "SELECT EXISTS (SELECT 1 FROM fdb_audit.audit_record WHERE sql = 'received SIGHUP and reloading configuration files')",
                      "返回 true，说明切换目录后当前外部表读取的是新目录生成的 reload 日志", rows_equal([["t"]])))
STEPS += _set_and_reload("SAO 修改审计日志文件前缀", "fdb.audit_log_prefix",
                         "fbase_regress_audit", "返回 fbase_regress_audit")
STEPS += _set_and_reload("SAO 将审计日志改为明文存储", "fdb.audit_log_encrypt",
                         "plain", "返回 plain")
STEPS += _set_and_reload("SAO 将审计日志改为 RC4 存储", "fdb.audit_log_encrypt",
                         "rc4", "返回 rc4")
STEPS += _set_and_reload("SAO 修改审计日志加密密钥", "fdb.audit_log_key",
                         "fbaseRegressKey", "返回 fbaseRegressKey")
STEPS += _set_and_reload("SAO 设置审计日志查询时间窗为 1 天", "fdb.audit_log_query_interval",
                         "1", "返回 1")


CASE = {
    "id": "mac.audit.log_configuration",
    "name": "SAO 修改审计日志目录、前缀、加密和查询时间窗",
    "document": "审计功能转测（邹雪、陈群友）.md",
    "section": "5.5.1-5.5.4",
    "group": "audit",
    "fixtures": [
        "cluster",
        {"type": "settings", "user": "sao", "setup": False, "restore_runtime_value": True,
         "values": {"fdb.audit_log_directory": "fdb_audit", "fdb.audit_log_prefix": "fdb_audit",
                    "fdb.audit_log_encrypt": "xor", "fdb.audit_log_key": "12345678",
                    "fdb.audit_log_query_interval": "0"}, "apply": "reload",
         "purpose": "恢复审计日志配置的用例前状态"},
    ],
    "requirements": {"plugins": ["fbase_mac"], "writable_node": True, "node": "primary", "roles": ["sao"]},
    "prerequisites": ["SAO 有权设置并重载审计日志配置项"],
    "steps": STEPS,
    "teardown": "settings fixture 将审计日志目录、前缀、加密方式、密钥和查询时间窗恢复至用例前的运行值并 reload。",
}
