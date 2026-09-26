from framework.assertions import output_contains, sql_fails
from framework.steps import sql_step
from suites.mac.cases.separation_of_duties.common import (
    COMMON_PREREQUISITES, separation_requirements,
)


CASE = {
    "id": "mac.separation_of_duties.dba_user_management_separation_on",
    "name": "三权分立开启时 DBA 用户管理限制",
    "document": "三权分立功能转测.md",
    "section": "5.3.1.2",
    "group": "separation_of_duties",
    "fixtures": [
        "cluster",
        {"type": "roles", "create": [
            {"name": "fbase_regress_managed", "attributes": ""},
        ]},
    ],
    "requirements": separation_requirements(),
    "prerequisites": COMMON_PREREQUISITES,
    "steps": [
        sql_step(
            title="三权分立开启时 DBA 可以创建无附加属性的普通用户",
            user="postgres",
            sql="BEGIN; CREATE USER fbase_regress_plain; ROLLBACK",
            expected="执行成功，返回 BEGIN、CREATE ROLE、ROLLBACK，且不保留测试用户",
            assertion=output_contains("BEGIN", "CREATE ROLE", "ROLLBACK"),
        ),
        sql_step(
            title="DBA 不能创建带 CREATEROLE 特权的用户",
            user="postgres",
            sql=("BEGIN; CREATE USER fbase_regress_privileged CREATEROLE; "
                 "ROLLBACK"),
            expected="执行失败，错误信息说明创建用户时不能指定任何附加参数",
            assertion=sql_fails("cannot create user with any parameters"),
        ),
        sql_step(
            title="DBA 不能在创建用户时设置密码",
            user="postgres",
            sql=("BEGIN; CREATE USER fbase_regress_password "
                 "PASSWORD 'Aa123456'; ROLLBACK"),
            expected="执行失败，错误信息说明创建用户时不能指定任何附加参数",
            assertion=sql_fails("cannot create user with any parameters"),
        ),
        sql_step(
            title="DBA 不能修改其他用户的 CREATEDB 特权",
            user="postgres",
            sql="BEGIN; ALTER USER fbase_regress_managed CREATEDB; ROLLBACK",
            expected="执行失败，错误信息说明三权分立开启时不能设置 CREATEDB",
            assertion=sql_fails("cannot set createdb configuration"),
        ),
    ],
    "teardown": "用户 DDL 均在事务内；roles fixture 自动删除测试角色",
}
