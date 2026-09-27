from framework.assertions import command_succeeds, output_contains, rows_equal, sql_fails
from framework.steps import sql_step
from suites.mac.cases.separation_of_duties.common import separation_requirements


ACTOR = "fbase_regress_createrole_off"


CASE = {
    "id": "mac.separation_of_duties.createrole_user_management_separation_off",
    "name": "三权分立关闭时 CREATEROLE 用户管理权限",
    "document": "三权分立功能转测.md",
    "section": "5.4.1.1",
    "group": "separation_of_duties",
    "fixtures": [
        "cluster",
        {"type": "settings", "user": "sso", "setup": False,
         "values": {"fdb.separate_user": "off"}, "apply": "reload",
         "purpose": "恢复三权分立开关的用例前状态"},
        {"type": "roles", "setup": False, "cleanup_priority": -10,
         "create": [{"name": ACTOR, "attributes": ""}]},
    ],
    "requirements": separation_requirements(roles=["sso", "sao"], require_enabled=False),
    "prerequisites": [
        "mac 集群已由平台创建并处于运行状态",
        "SSO 有权设置并重载 fdb.separate_user",
        "普通 CREATEROLE 用户仅能分配自身权限范围内的属性",
    ],
    "steps": [
        sql_step("SSO 关闭三权分立机制", "sso", "ALTER SYSTEM SET fdb.separate_user = off",
                 "ALTER SYSTEM 执行成功", command_succeeds()),
        {"type": "cluster_action", "title": "重载三权分立配置", "action": "reload",
         "expected": "reload 执行成功", "assertion": command_succeeds()},
        sql_step("确认三权分立已关闭", "postgres", "SHOW fdb.separate_user", "返回 off",
                 rows_equal([["off"]])),
        sql_step("DBA 创建具备 CREATEROLE 的普通测试用户", "postgres",
                 "CREATE USER %s CREATEROLE" % ACTOR,
                 "返回 CREATE ROLE", command_succeeds()),
        sql_step("CREATEROLE 用户可创建带 CREATEDB 属性的用户", ACTOR,
                 "BEGIN; CREATE USER fbase_regress_cr_off_createdb CREATEDB; ROLLBACK",
                 "创建成功且事务回滚后不保留用户",
                 output_contains("BEGIN", "CREATE ROLE", "ROLLBACK")),
        sql_step("CREATEROLE 用户可创建带 INHERIT 属性的用户", ACTOR,
                 "BEGIN; CREATE USER fbase_regress_cr_off_inherit INHERIT; ROLLBACK",
                 "创建成功且事务回滚后不保留用户",
                 output_contains("BEGIN", "CREATE ROLE", "ROLLBACK")),
        sql_step("CREATEROLE 用户可创建由自身管理的用户", ACTOR,
                 "BEGIN; CREATE USER fbase_regress_cr_off_admin ADMIN %s; ROLLBACK" % ACTOR,
                 "创建成功且事务回滚后不保留用户",
                 output_contains("BEGIN", "CREATE ROLE", "ROLLBACK")),
        sql_step("CREATEROLE 用户不能创建关联 SSO 的用户", ACTOR,
                 "BEGIN; CREATE USER fbase_regress_cr_off_role_sso ROLE sso; ROLLBACK",
                 "执行失败，不能关联等保管理员", sql_fails("related to mac administrators")),
        sql_step("CREATEROLE 用户不能创建超级用户", ACTOR,
                 "BEGIN; CREATE USER fbase_regress_cr_off_super SUPERUSER; ROLLBACK",
                 "执行失败，只有超级用户可创建超级用户", sql_fails("must be superuser to create superusers")),
        sql_step("CREATEROLE 用户不能创建 BYPASSRLS 用户", ACTOR,
                 "BEGIN; CREATE USER fbase_regress_cr_off_bypass BYPASSRLS; ROLLBACK",
                 "执行失败，只有超级用户可创建 BYPASSRLS 用户", sql_fails("must be superuser to create bypassrls users")),
        sql_step("CREATEROLE 用户不能创建复制用户", ACTOR,
                 "BEGIN; CREATE USER fbase_regress_cr_off_repl REPLICATION; ROLLBACK",
                 "执行失败，只有超级用户可创建复制用户", sql_fails("must be superuser to create replication users")),
        sql_step("CREATEROLE 用户可修改普通用户 CREATEDB 属性", ACTOR,
                 "BEGIN; CREATE USER fbase_regress_cr_off_alter; ALTER USER fbase_regress_cr_off_alter CREATEDB; "
                 "SELECT rolcreatedb FROM pg_roles WHERE rolname = 'fbase_regress_cr_off_alter'; ROLLBACK",
                 "返回 true，且事务回滚后不保留属性修改",
                 output_contains("BEGIN", "CREATE ROLE", "ALTER ROLE", "t", "ROLLBACK")),
        sql_step("CREATEROLE 用户可修改普通用户密码", ACTOR,
                 "BEGIN; CREATE USER fbase_regress_cr_off_password; "
                 "ALTER USER fbase_regress_cr_off_password PASSWORD 'A1a123456'; ROLLBACK",
                 "返回 ALTER ROLE，且事务回滚后不保留密码修改",
                 output_contains("BEGIN", "CREATE ROLE", "ALTER ROLE", "ROLLBACK")),
    ],
    "teardown": "roles fixture 删除 CREATEROLE 测试用户；settings fixture 恢复 fdb.separate_user 并 reload。",
}
