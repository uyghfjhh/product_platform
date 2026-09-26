from framework.assertions import command_succeeds, rows_equal, sql_fails
from framework.steps import sql_step


USER = "fbase_regress_audit_event_user"
TABLE = "fbase_regress_audit_event_table"
SUCCESS_RULE = "fbase_regress_audit_select_success"
FAIL_RULE = "fbase_regress_audit_select_fail"
OBJECT_RULE = "fbase_regress_audit_update_object"


CASE = {
    "id": "mac.audit.statement_and_object_logs",
    "name": "语句级和对象级审计日志记录",
    "document": "审计功能转测（邹雪、陈群友）.md",
    "section": "5.4.3-5.4.4",
    "group": "audit",
    "fixtures": [
        "cluster",
        {"type": "settings", "user": "sao", "values": {"fdb.enable_audit": "1"},
         "apply": "reload", "restore_runtime_value": True,
         "purpose": "启用审计日志记录所需的审计开关"},
        {"type": "roles", "create": [{"name": USER, "attributes": "", "login": True}],
         "cleanup_priority": -10},
        {"type": "table", "name": TABLE, "columns": "id integer PRIMARY KEY, name text",
         "cleanup_priority": 10},
        {"type": "table_grants", "table": TABLE, "grants": {USER: "SELECT, UPDATE"}},
        {"type": "audit_rules", "rules": [
            {"owner": "sao", "name": SUCCESS_RULE, "kind": "stmt"},
            {"owner": "sao", "name": FAIL_RULE, "kind": "stmt"},
            {"owner": "sao", "name": OBJECT_RULE, "kind": "object"},
        ]},
    ],
    "requirements": {"plugins": ["fbase_mac"], "writable_node": True, "node": "primary",
                     "roles": ["sao"]},
    "prerequisites": ["审计功能开启且 SAO 可以读取 fdb_audit.audit_records"],
    "steps": [
        sql_step("DBA 写入对象级审计测试数据", "postgres",
                 "INSERT INTO %s VALUES (1, 'before')" % TABLE, "返回 INSERT 0 1", command_succeeds()),
        sql_step("SAO 设置 SELECT 成功审计规则", "sao",
                 "SELECT fdb_audit.set_audit_stmt('%s', 'SELECT', '%s', 'SUCCESS')" % (SUCCESS_RULE, USER),
                 "规则创建成功", command_succeeds()),
        sql_step("SAO 设置 SELECT 失败审计规则", "sao",
                 "SELECT fdb_audit.set_audit_stmt('%s', 'SELECT', '%s', 'FAIL')" % (FAIL_RULE, USER),
                 "规则创建成功", command_succeeds()),
        sql_step("SAO 设置表 UPDATE 对象级审计规则", "sao",
                 "SELECT fdb_audit.set_audit_object('%s', 'UPDATE', '%s', 'ALL', 'TABLE', 'public', '%s')" %
                 (OBJECT_RULE, USER, TABLE), "规则创建成功", command_succeeds()),
        sql_step("普通用户执行成功的 SELECT", USER,
                 "SELECT id::text, name FROM %s WHERE id = 1" % TABLE,
                 "返回测试数据", rows_equal([["1", "before"]])),
        sql_step("普通用户执行失败的 SELECT", USER,
                 "SELECT * FROM fbase_regress_audit_missing_table",
                 "执行失败，触发 SELECT FAIL 审计", sql_fails("does not exist")),
        sql_step("普通用户执行对象级 UPDATE", USER,
                 "UPDATE %s SET name = 'after' WHERE id = 1" % TABLE,
                 "返回 UPDATE 1", command_succeeds()),
        sql_step("SAO 查到 SELECT 成功审计记录", "sao",
                 "SELECT EXISTS (SELECT 1 FROM fdb_audit.audit_records WHERE username = '%s' AND audittype = 'SELECT' AND succ = 'SUCCESS' AND sql LIKE '%%fbase_regress_audit_event_table%%')" % USER,
                 "返回 true", rows_equal([["t"]])),
        sql_step("SAO 查到 SELECT 失败审计记录", "sao",
                 "SELECT EXISTS (SELECT 1 FROM fdb_audit.audit_records WHERE username = '%s' AND audittype = 'SELECT' AND succ = 'FAILED' AND sql LIKE '%%fbase_regress_audit_missing_table%%')" % USER,
                 "返回 true", rows_equal([["t"]])),
        sql_step("SAO 查到表 UPDATE 对象级审计记录", "sao",
                 "SELECT EXISTS (SELECT 1 FROM fdb_audit.audit_records WHERE username = '%s' AND audittype = 'UPDATE' AND objname = '%s' AND succ = 'SUCCESS')" %
                 (USER, TABLE), "返回 true", rows_equal([["t"]])),
    ],
    "teardown": "audit_rules fixture 删除三条规则；table、授权和普通用户 fixture 删除测试对象；settings fixture 恢复审计开关。",
}
