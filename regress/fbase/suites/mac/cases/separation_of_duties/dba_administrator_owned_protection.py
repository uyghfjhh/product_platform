from framework.assertions import sql_fails
from framework.steps import sql_step
from suites.mac.cases.separation_of_duties.common import (
    COMMON_PREREQUISITES, separation_requirements,
)


CASE = {
    "id": "mac.separation_of_duties.dba_administrator_owned_protection",
    "name": "DBA 禁止撤销 SSO 和 SAO 的所有者身份",
    "document": "三权分立功能转测.md",
    "section": "5.3.4.12",
    "group": "separation_of_duties",
    "fixtures": ["cluster", "roles"],
    "requirements": separation_requirements(["sso", "sao"]),
    "prerequisites": COMMON_PREREQUISITES + ["数据库初始化角色 sso、sao 存在"],
    "steps": [
        sql_step(
            title="DBA 不能重新指派 SSO 所拥有对象",
            user="postgres", sql="REASSIGN OWNED BY sso TO postgres",
            expected="执行失败，错误信息说明不能重新指派 sso 或 sao 所拥有对象",
            assertion=sql_fails("sso and sao can not be reassign owned"),
        ),
        sql_step(
            title="DBA 不能删除 SSO 所拥有对象",
            user="postgres", sql="DROP OWNED BY sso",
            expected="执行失败，错误信息说明不能删除 sso 或 sao 所拥有对象",
            assertion=sql_fails("sso and sao can not be drop owned"),
        ),
        sql_step(
            title="DBA 不能重新指派 SAO 所拥有对象",
            user="postgres", sql="REASSIGN OWNED BY sao TO postgres",
            expected="执行失败，错误信息说明不能重新指派 sso 或 sao 所拥有对象",
            assertion=sql_fails("sso and sao can not be reassign owned"),
        ),
    ],
    "teardown": "所有 REASSIGN OWNED 和 DROP OWNED 均预期失败，不修改对象所有权",
}
