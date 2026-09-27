from framework.assertions import sql_fails
from framework.steps import sql_step
from suites.mac.cases.separation_of_duties.common import (
    COMMON_PREREQUISITES, separation_requirements,
)


CASE = {
    "id": "mac.separation_of_duties.dba_session_switch_restrictions",
    "name": "DBA 禁止切换到 SSO 和 SAO",
    "document": "三权分立功能转测.md",
    "section": "5.3.4.1",
    "group": "separation_of_duties",
    "fixtures": ["cluster", "roles"],
    "requirements": separation_requirements(["sso", "sao"]),
    "prerequisites": COMMON_PREREQUISITES + ["数据库初始化角色 sso、sao 存在"],
    "steps": [
        sql_step(
            title="DBA 不能 SET ROLE 到 SSO",
            user="postgres", sql="SET ROLE sso",
            expected="执行失败，错误信息说明不能切换到 sso 或 sao",
            assertion=sql_fails("switch session to sso or sao"),
        ),
        sql_step(
            title="DBA 不能 SET ROLE 到 SAO",
            user="postgres", sql="SET ROLE sao",
            expected="执行失败，错误信息说明不能切换到 sso 或 sao",
            assertion=sql_fails("switch session to sso or sao"),
        ),
        sql_step(
            title="DBA 不能 SET SESSION AUTHORIZATION 到 SSO",
            user="postgres", sql="SET SESSION AUTHORIZATION sso",
            expected="执行失败，错误信息说明不能切换到 sso 或 sao",
            assertion=sql_fails("switch session to sso or sao"),
        ),
        sql_step(
            title="DBA 不能 SET SESSION AUTHORIZATION 到 SAO",
            user="postgres", sql="SET SESSION AUTHORIZATION sao",
            expected="执行失败，错误信息说明不能切换到 sso 或 sao",
            assertion=sql_fails("switch session to sso or sao"),
        ),
    ],
    "teardown": "所有会话切换均预期失败，无需清理",
}
