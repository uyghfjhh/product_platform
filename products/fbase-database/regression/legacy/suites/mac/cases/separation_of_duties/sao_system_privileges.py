from framework.assertions import output_contains, rows_equal, sql_error
from framework.steps import sql_step
from suites.mac.cases.separation_of_duties.common import (
    COMMON_PREREQUISITES, separation_requirements,
)


CASE = {
    "id": "mac.separation_of_duties.sao_system_privileges",
    "name": "SAO 系统权限限制",
    "document": "三权分立功能转测.md",
    "section": "5.2.1",
    "group": "separation_of_duties",
    "fixtures": ["cluster", "roles"],
    "requirements": separation_requirements(["sao"]),
    "prerequisites": COMMON_PREREQUISITES + ["数据库初始化角色 sao 存在"],
    "steps": [
        sql_step(
            title="检查 SAO 的初始系统权限",
            user="postgres",
            sql=(
                "SELECT rolname,rolsuper,rolcreatedb,rolcreaterole,"
                "rolreplication,rolbypassrls,rolcanlogin "
                "FROM pg_roles WHERE rolname = 'sao'"
            ),
            expected=(
                "返回 sao|f|f|f|f|f|t：SAO 可登录，但不是超级用户，且不具备 "
                "CREATEDB、CREATEROLE、REPLICATION、BYPASSRLS 特权"
            ),
            assertion=rows_equal([["sao", "f", "f", "f", "f", "f", "t"]]),
        ),
        sql_step(
            title="SAO 不能修改自己的 LOGIN 属性",
            user="sao",
            sql="ALTER USER sao LOGIN",
            expected="执行失败，SQLSTATE=42501，错误信息包含 permission denied",
            assertion=sql_error("42501", "permission denied"),
        ),
        sql_step(
            title="SAO 不能修改自己的 INHERIT 属性",
            user="sao",
            sql="ALTER USER sao INHERIT",
            expected="执行失败，SQLSTATE=42501，错误信息包含 permission denied",
            assertion=sql_error("42501", "permission denied"),
        ),
        sql_step(
            title="SAO 可以修改自己的密码",
            user="sao",
            sql="BEGIN; ALTER USER sao PASSWORD 'Sao12345'; ROLLBACK",
            expected="执行成功，依次返回 BEGIN、ALTER ROLE、ROLLBACK；不保留密码修改",
            assertion=output_contains("BEGIN", "ALTER ROLE", "ROLLBACK"),
        ),
    ],
    "teardown": "密码修改在业务步骤事务内回滚，无额外清理动作",
}
