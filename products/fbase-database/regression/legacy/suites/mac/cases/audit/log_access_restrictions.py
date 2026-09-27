from framework.assertions import command_succeeds, sql_fails
from framework.steps import sql_step


USER = "fbase_regress_audit_log_user"


CASE = {
    "id": "mac.audit.log_access_restrictions",
    "name": "审计日志查看权限限制",
    "document": "审计功能转测（邹雪、陈群友）.md",
    "section": "5.4.1",
    "group": "audit",
    "fixtures": [
        "cluster",
        {"type": "roles", "create": [{"name": USER, "attributes": "", "login": True}]},
    ],
    "requirements": {"plugins": ["fbase_mac"], "writable_node": True, "node": "primary",
                     "roles": ["sso", "sao"]},
    "prerequisites": ["fdb_audit.audit_record 外部表和 audit_records 视图存在"],
    "steps": [
        sql_step("超级用户不能通过审计日志视图查看记录", "postgres",
                 "SELECT * FROM fdb_audit.audit_records", "执行失败，仅 SAO/SSO 可调用审计日志视图",
                 sql_fails("must be SAO/SSO user can execute this function")),
        sql_step("超级用户不能直接查看审计日志外部表", "postgres",
                 "SELECT * FROM fdb_audit.audit_record", "执行失败，仅 SAO/SSO 可查看审计日志",
                 sql_fails("must be SAO/SSO user can execute this function")),
        sql_step("普通用户不能直接查看审计日志外部表", USER,
                 "SELECT * FROM fdb_audit.audit_record", "执行失败，普通用户无 fdb_audit schema 使用权限",
                 sql_fails("permission denied for schema fdb_audit")),
        sql_step("普通用户不能通过审计日志视图查看记录", USER,
                 "SELECT * FROM fdb_audit.audit_records", "执行失败，普通用户无 fdb_audit schema 使用权限",
                 sql_fails("permission denied for schema fdb_audit")),
        sql_step("SSO 可通过审计日志视图查询其可见记录", "sso",
                 "SELECT count(*) FROM fdb_audit.audit_records", "查询执行成功", command_succeeds()),
        sql_step("SAO 可通过审计日志视图查询其可见记录", "sao",
                 "SELECT count(*) FROM fdb_audit.audit_records", "查询执行成功", command_succeeds()),
    ],
    "teardown": "roles fixture 删除普通测试用户。",
}
