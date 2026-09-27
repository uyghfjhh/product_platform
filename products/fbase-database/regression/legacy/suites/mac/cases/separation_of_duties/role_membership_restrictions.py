from framework.assertions import sql_fails
from framework.steps import sql_step
from suites.mac.cases.separation_of_duties.common import (
    COMMON_PREREQUISITES, separation_requirements,
)


CASE = {
    "id": "mac.separation_of_duties.role_membership_restrictions",
    "name": "三权分立开启时禁止角色成员关系变更",
    "document": "三权分立功能转测.md",
    "section": "5.3.3",
    "group": "separation_of_duties",
    "fixtures": [
        "cluster",
        {"type": "roles", "create": [
            {"name": "fbase_regress_parent", "attributes": ""},
            {"name": "fbase_regress_member", "attributes": ""},
        ]},
    ],
    "requirements": separation_requirements(),
    "prerequisites": COMMON_PREREQUISITES,
    "steps": [
        sql_step(
            title="DBA 不能授予角色成员关系",
            user="postgres",
            sql=("BEGIN; GRANT fbase_regress_parent TO fbase_regress_member; "
                 "ROLLBACK"),
            expected="执行失败，错误信息说明三权分立开启时禁止该命令",
            assertion=sql_fails("separation of powers is on"),
        ),
        sql_step(
            title="DBA 不能回收角色成员关系",
            user="postgres",
            sql=("BEGIN; REVOKE fbase_regress_parent FROM fbase_regress_member; "
                 "ROLLBACK"),
            expected="执行失败，错误信息说明三权分立开启时禁止该命令",
            assertion=sql_fails("separation of powers is on"),
        ),
    ],
    "teardown": "角色由 roles fixture 自动删除；GRANT/REVOKE 均在事务内执行",
}
