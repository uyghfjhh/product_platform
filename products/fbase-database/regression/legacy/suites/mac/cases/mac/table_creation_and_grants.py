from framework.assertions import command_succeeds, rows_equal
from framework.steps import sql_step


TABLE = "fbase_regress_mac_grant_table"
U1 = "fbase_regress_mac_grant_u1"
U2 = "fbase_regress_mac_grant_u2"


CASE = {
    "id": "mac.mac.table_creation_and_grants",
    "name": "MAC 表创建及用户授权",
    "document": "安可强制访问控制功能转测(张娟).md",
    "section": "2.2.9",
    "group": "mac",
    "fixtures": [
        "cluster",
        {"type": "table", "setup": False, "cleanup_priority": 10, "name": TABLE},
        {"type": "roles", "create": [
            {"name": U1, "attributes": ""},
            {"name": U2, "attributes": ""},
        ]},
    ],
    "requirements": {"writable_node": True, "node": "primary"},
    "prerequisites": [
        "mac 集群已由平台创建并处于运行状态",
        "管理员可以创建测试表并向普通用户授予表权限",
    ],
    "steps": [
        sql_step("DBA 创建测试表 T1", "postgres",
                 "CREATE TABLE %s (a integer, name varchar)" % TABLE,
                 "返回 CREATE TABLE", command_succeeds()),
        sql_step("向 U1 授予 SELECT 权限", "postgres",
                 "GRANT SELECT ON %s TO %s" % (TABLE, U1),
                 "返回 GRANT", command_succeeds()),
        sql_step("向 U2 授予 SELECT 权限", "postgres",
                 "GRANT SELECT ON %s TO %s" % (TABLE, U2),
                 "返回 GRANT", command_succeeds()),
        sql_step("向 U1 授予 INSERT 权限", "postgres",
                 "GRANT INSERT ON %s TO %s" % (TABLE, U1),
                 "返回 GRANT", command_succeeds()),
        sql_step("向 U2 授予 INSERT 权限", "postgres",
                 "GRANT INSERT ON %s TO %s" % (TABLE, U2),
                 "返回 GRANT", command_succeeds()),
        sql_step("确认 U1、U2 均获得 SELECT 和 INSERT 权限", "postgres",
                 "SELECT grantee, privilege_type FROM information_schema.role_table_grants "
                 "WHERE table_schema = 'public' AND table_name = '%s' "
                 "AND grantee IN ('%s', '%s') AND privilege_type IN ('SELECT', 'INSERT') "
                 "ORDER BY grantee, privilege_type" % (TABLE, U1, U2),
                 "依次返回 U1、U2 的 INSERT 与 SELECT 授权",
                 rows_equal([[U1, "INSERT"], [U1, "SELECT"],
                             [U2, "INSERT"], [U2, "SELECT"]])),
    ],
    "teardown": "table fixture 删除测试表；roles fixture 删除测试用户。",
}
