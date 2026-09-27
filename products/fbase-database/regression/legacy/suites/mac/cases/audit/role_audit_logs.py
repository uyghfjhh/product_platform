import uuid

from framework.assertions import command_succeeds, rows_equal, sql_fails
from framework.steps import sql_step


# Audit records are retained by the product.  Per-process names keep a new
# run's assertion independent of earlier test evidence.
TOKEN = uuid.uuid4().hex[:12]
U1 = "fbase_regress_audit_role_u1_%s" % TOKEN
U2 = "fbase_regress_audit_role_u2_%s" % TOKEN
MISSING = "fbase_regress_audit_role_missing_%s" % TOKEN
RULE = "fbase_regress_audit_role_rule"


CASE = {
    "id": "mac.audit.role_audit_logs",
    "name": "创建和修改用户的 ROLE 审计日志",
    "document": "审计功能转测（邹雪、陈群友）.md",
    "section": "5.6",
    "group": "audit",
    "fixtures": [
        "cluster",
        {"type": "settings", "user": "sao", "values": {"fdb.enable_audit": "1"},
         "apply": "reload", "restore_runtime_value": True,
         "purpose": "启用 ROLE 审计规则所需的审计开关"},
        {"type": "settings", "user": "sso", "setup": False,
         "values": {"fdb.separate_user": "off"}, "apply": "reload",
         "purpose": "恢复三权分立开关的用例前状态"},
        {"type": "roles", "setup": False, "cleanup_priority": -10,
         "create": [{"name": U1, "attributes": ""}, {"name": U2, "attributes": ""}]},
        {"type": "audit_rules", "rules": [{"owner": "sao", "name": RULE, "kind": "stmt"}]},
    ],
    "requirements": {"plugins": ["fbase_mac"], "writable_node": True, "node": "primary", "roles": ["sso", "sao"]},
    "prerequisites": ["SAO 可以设置 ROLE 审计规则，DBA 可执行角色管理命令"],
    "steps": [
        sql_step("SSO 关闭三权分立以执行文档中的带密码 CREATE ROLE", "sso",
                 "ALTER SYSTEM SET fdb.separate_user = off", "返回 ALTER SYSTEM", command_succeeds()),
        {"type": "cluster_action", "title": "重载三权分立配置", "action": "reload",
         "expected": "reload 执行成功", "assertion": command_succeeds()},
        sql_step("SAO 设置审计 postgres 的 ROLE 规则", "sao",
                 "SELECT fdb_audit.set_audit_stmt('%s', 'ROLE', 'postgres', 'ALL')" % RULE,
                 "规则创建成功", command_succeeds()),
        sql_step("DBA 创建带密码的角色 U1", "postgres",
                 "CREATE ROLE %s WITH LOGIN PASSWORD 'Aa123456'" % U1,
                 "返回 CREATE ROLE", command_succeeds()),
        sql_step("DBA 重复创建 U1 失败", "postgres",
                 "CREATE ROLE %s WITH LOGIN PASSWORD 'Aa123456'" % U1,
                 "执行失败，角色已存在", sql_fails("already exists")),
        sql_step("DBA 创建带有效期的角色 U2", "postgres",
                 "CREATE ROLE %s WITH LOGIN PASSWORD 'Aa123456' VALID UNTIL '2099-01-01'" % U2,
                 "返回 CREATE ROLE", command_succeeds()),
        sql_step("DBA 重复创建 U2 失败", "postgres",
                 "CREATE ROLE %s WITH LOGIN PASSWORD 'Aa123456' VALID UNTIL '2099-01-01'" % U2,
                 "执行失败，角色已存在", sql_fails("already exists")),
        sql_step("DBA 修改 U1 的密码和有效期", "postgres",
                 "ALTER ROLE %s PASSWORD 'Bb123456' VALID UNTIL '2099-01-01'" % U1,
                 "返回 ALTER ROLE", command_succeeds()),
        sql_step("DBA 修改不存在角色失败", "postgres",
                 "ALTER ROLE %s PASSWORD 'Bb123456' VALID UNTIL '2099-01-01'" % MISSING,
                 "执行失败，角色不存在", sql_fails("does not exist")),
        sql_step("SAO 查到 ROLE 审计的成功和失败记录", "sao",
                 "SELECT succ, count(*)::text FROM fdb_audit.audit_records "
                 "WHERE audittype = 'ROLE' AND username = 'postgres' "
                 "AND objname IN ('%s', '%s', '%s') GROUP BY succ ORDER BY succ" % (U1, U2, MISSING),
                 "返回 FAILED=3、SUCCESS=3", rows_equal([["FAILED", "3"], ["SUCCESS", "3"]])),
    ],
    "teardown": "audit_rules fixture 删除 ROLE 规则；roles fixture 删除 U1、U2；两个 settings fixture 恢复审计和三权分立配置。",
}
