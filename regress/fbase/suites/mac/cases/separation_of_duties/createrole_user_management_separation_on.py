from framework.assertions import command_succeeds, output_contains, rows_equal, sql_fails
from framework.steps import sql_step
from suites.mac.cases.separation_of_duties.common import separation_requirements


ACTOR = "fbase_regress_createrole_on"
TARGET = "fbase_regress_createrole_on_target"


CASE = {
    "id": "mac.separation_of_duties.createrole_user_management_separation_on",
    "name": "三权分立开启时 CREATEROLE 用户管理限制",
    "document": "三权分立功能转测.md",
    "section": "5.4.1.2",
    "group": "separation_of_duties",
    "fixtures": [
        "cluster",
        {"type": "settings", "user": "sso", "setup": False,
         "values": {"fdb.separate_user": "on"}, "apply": "reload",
         "purpose": "恢复三权分立开关的用例前状态"},
        {"type": "roles", "setup": False, "cleanup_priority": -10,
         "create": [{"name": ACTOR, "attributes": ""}, {"name": TARGET, "attributes": ""}]},
    ],
    "requirements": separation_requirements(roles=["sso"]),
    "prerequisites": [
        "mac 集群已由平台创建并处于运行状态",
        "SSO 有权设置并重载 fdb.separate_user",
    ],
    "steps": [
        sql_step("SSO 临时关闭三权分立以创建 CREATEROLE 测试用户", "sso",
                 "ALTER SYSTEM SET fdb.separate_user = off", "ALTER SYSTEM 执行成功", command_succeeds()),
        {"type": "cluster_action", "title": "重载关闭后的三权分立配置", "action": "reload",
         "expected": "reload 执行成功", "assertion": command_succeeds()},
        sql_step("DBA 创建 CREATEROLE 测试用户", "postgres",
                 "CREATE USER %s CREATEROLE" % ACTOR, "返回 CREATE ROLE", command_succeeds()),
        sql_step("DBA 创建普通目标用户", "postgres", "CREATE USER %s" % TARGET,
                 "返回 CREATE ROLE", command_succeeds()),
        sql_step("SSO 开启三权分立机制", "sso", "ALTER SYSTEM SET fdb.separate_user = on",
                 "ALTER SYSTEM 执行成功", command_succeeds()),
        {"type": "cluster_action", "title": "重载开启后的三权分立配置", "action": "reload",
         "expected": "reload 执行成功", "assertion": command_succeeds()},
        sql_step("确认三权分立已开启", "postgres", "SHOW fdb.separate_user", "返回 on",
                 rows_equal([["on"]])),
        sql_step("CREATEROLE 用户可创建无附加属性的普通用户", ACTOR,
                 "BEGIN; CREATE USER fbase_regress_cr_on_plain; ROLLBACK",
                 "创建成功且事务回滚后不保留用户", output_contains("BEGIN", "CREATE ROLE", "ROLLBACK")),
        sql_step("CREATEROLE 用户不能创建带 CREATEROLE 属性的用户", ACTOR,
                 "BEGIN; CREATE USER fbase_regress_cr_on_privileged CREATEROLE; ROLLBACK",
                 "执行失败，开启三权分立时创建用户不能带参数",
                 sql_fails("cannot create user with any parameters")),
        sql_step("CREATEROLE 用户不能在创建用户时设置密码", ACTOR,
                 "BEGIN; CREATE USER fbase_regress_cr_on_password PASSWORD 'Aa123456'; ROLLBACK",
                 "执行失败，开启三权分立时创建用户不能带参数",
                 sql_fails("cannot create user with any parameters")),
        sql_step("CREATEROLE 用户可修改自身 LOGIN 属性", ACTOR,
                 "BEGIN; ALTER USER %s LOGIN; ROLLBACK" % ACTOR,
                 "返回 BEGIN、ALTER ROLE、ROLLBACK", output_contains("BEGIN", "ALTER ROLE", "ROLLBACK")),
        sql_step("CREATEROLE 用户不能修改自身 CREATEDB 属性", ACTOR,
                 "ALTER USER %s CREATEDB" % ACTOR,
                 "执行失败，开启三权分立时不能设置 CREATEDB",
                 sql_fails("cannot set createdb configuration when separation of powers is on")),
        sql_step("CREATEROLE 用户不能修改其他用户 CREATEDB 属性", ACTOR,
                 "ALTER USER %s CREATEDB" % TARGET,
                 "执行失败，只允许目标用户修改自身配置", 
                 sql_fails("only %s can change itself's configurations when separation of powers is on" % TARGET)),
        sql_step("CREATEROLE 用户不能修改其他用户密码", ACTOR,
                 "ALTER USER %s PASSWORD 'A1a123456'" % TARGET,
                 "执行失败，只允许目标用户修改自身配置",
                 sql_fails("only %s can change itself's configurations when separation of powers is on" % TARGET)),
    ],
    "teardown": "roles fixture 删除 CREATEROLE 测试用户及普通目标用户；settings fixture 恢复 fdb.separate_user 并 reload。",
}
