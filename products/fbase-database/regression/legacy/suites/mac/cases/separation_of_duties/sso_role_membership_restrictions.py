from framework.assertions import sql_fails
from framework.steps import sql_step
from suites.mac.cases.separation_of_duties.common import (
    COMMON_PREREQUISITES, separation_requirements,
)


CASE = {
    "id": "mac.separation_of_duties.sso_role_membership_restrictions",
    "name": "SSO 禁止分配和回收角色关系",
    "document": "三权分立功能转测.md",
    "section": "5.1.3",
    "group": "separation_of_duties",
    "fixtures": [
        "cluster",
        {"type": "roles", "create": [
            {"name": "fbase_regress_sso_parent", "attributes": ""},
            {"name": "fbase_regress_sso_member", "attributes": ""},
        ]},
    ],
    "requirements": separation_requirements(["sso"]),
    "prerequisites": COMMON_PREREQUISITES + ["数据库初始化角色 sso 存在"],
    "steps": [
        sql_step(
            title="SSO 不能授予角色成员关系",
            user="sso",
            sql=("BEGIN; GRANT fbase_regress_sso_parent "
                 "TO fbase_regress_sso_member; ROLLBACK"),
            expected="执行失败，错误信息说明三权分立开启时禁止该命令",
            assertion=sql_fails("separation of powers is on"),
        ),
        sql_step(
            title="SSO 不能回收角色成员关系",
            user="sso",
            sql=("BEGIN; REVOKE fbase_regress_sso_parent "
                 "FROM fbase_regress_sso_member; ROLLBACK"),
            expected="执行失败，错误信息说明三权分立开启时禁止该命令",
            assertion=sql_fails("separation of powers is on"),
        ),
    ],
    "teardown": "角色由 roles fixture 自动删除；GRANT/REVOKE 均在事务内执行",
}
