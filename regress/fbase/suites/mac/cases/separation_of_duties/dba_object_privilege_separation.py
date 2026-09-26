from framework.assertions import command_succeeds, rows_equal, sql_fails
from framework.steps import sql_step
from suites.mac.cases.separation_of_duties.common import (
    COMMON_PREREQUISITES, separation_requirements,
)


TABLE = "fbase_regress_dba_acl_table"
ROLE = "fbase_regress_dba_acl_role"


CASE = {
    "id": "mac.separation_of_duties.dba_object_privilege_separation",
    "name": "DBA 对普通对象和等保对象的授权边界",
    "document": "三权分立功能转测.md",
    "section": "5.3.2",
    "group": "separation_of_duties",
    "fixtures": [
        "cluster",
        {"type": "roles", "setup": False, "cleanup_priority": -10,
         "create": [{"name": ROLE, "attributes": ""}]},
        {"type": "table", "setup": False, "cleanup_priority": 0, "name": TABLE},
    ],
    "requirements": separation_requirements(["sso", "sao"]),
    "prerequisites": COMMON_PREREQUISITES + [
        "fdb_mac.policy 安全元数据表和 fdb_audit.audit_rule 审计元数据表存在",
    ],
    "steps": [
        sql_step("DBA 创建普通测试角色", "postgres", "CREATE ROLE %s" % ROLE,
                 "返回 CREATE ROLE", command_succeeds()),
        sql_step("DBA 创建普通测试表", "postgres", "CREATE TABLE %s(id integer)" % TABLE,
                 "返回 CREATE TABLE", command_succeeds()),
        sql_step("DBA 可向普通角色授予普通表 SELECT 权限", "postgres",
                 "GRANT SELECT ON %s TO %s" % (TABLE, ROLE),
                 "返回 GRANT", command_succeeds()),
        sql_step("确认普通对象 SELECT 授权已生效", "postgres",
                 "SELECT has_table_privilege('%s', '%s', 'SELECT')" % (ROLE, TABLE),
                 "返回 true", rows_equal([["t"]])),
        sql_step("DBA 可回收普通角色的普通表 SELECT 权限", "postgres",
                 "REVOKE SELECT ON %s FROM %s" % (TABLE, ROLE),
                 "返回 REVOKE", command_succeeds()),
        sql_step("确认普通对象 SELECT 授权已回收", "postgres",
                 "SELECT has_table_privilege('%s', '%s', 'SELECT')" % (ROLE, TABLE),
                 "返回 false", rows_equal([["f"]])),
        sql_step("DBA 不能授予 MAC 元数据表权限", "postgres",
                 "GRANT SELECT ON fdb_mac.policy TO %s" % ROLE,
                 "执行失败，不能授予或回收 MAC 对象权限",
                 sql_fails("cannot grant or revoke mac object(fdb_mac.policy)")),
        sql_step("DBA 不能授予审计元数据表权限", "postgres",
                 "GRANT SELECT ON fdb_audit.audit_rule TO %s" % ROLE,
                 "执行失败，不能授予或回收 MAC 保护的审计对象权限",
                 sql_fails("cannot grant or revoke mac object(fdb_audit.audit_rule)")),
        sql_step("DBA 不能向 SSO 授予普通表权限", "postgres",
                 "GRANT SELECT ON %s TO sso" % TABLE,
                 "执行失败，不能向 SSO 授权", sql_fails("cannot grant or revoke sso")),
        sql_step("DBA 不能向 SAO 授予普通表权限", "postgres",
                 "GRANT SELECT ON %s TO sao" % TABLE,
                 "执行失败，不能向 SAO 授权", sql_fails("cannot grant or revoke sao")),
        sql_step("DBA 不能从 SSO 回收普通表权限", "postgres",
                 "REVOKE SELECT ON %s FROM sso" % TABLE,
                 "执行失败，不能从 SSO 回收权限", sql_fails("cannot grant or revoke sso")),
        sql_step("DBA 不能从 SAO 回收普通表权限", "postgres",
                 "REVOKE SELECT ON %s FROM sao" % TABLE,
                 "执行失败，不能从 SAO 回收权限", sql_fails("cannot grant or revoke sao")),
    ],
    "teardown": "table fixture 先删除测试表；roles fixture 随后删除普通测试角色。",
}
