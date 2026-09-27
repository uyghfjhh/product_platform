from framework.assertions import sql_fails
from framework.steps import sql_step
from suites.mac.cases.separation_of_duties.common import (
    COMMON_PREREQUISITES, separation_requirements,
)


CASE = {
    "id": "mac.separation_of_duties.dba_security_configuration_restrictions",
    "name": "DBA 禁止设置等保安全与审计配置",
    "document": "三权分立功能转测.md",
    "section": "5.3.4.8",
    "group": "separation_of_duties",
    "fixtures": ["cluster"],
    "requirements": separation_requirements(),
    "prerequisites": COMMON_PREREQUISITES,
    "steps": [
        sql_step(
            title="DBA 不能设置强制访问控制开关",
            user="postgres", sql="ALTER SYSTEM SET fdb.enable_mac = on",
            expected="执行失败，错误信息说明只有 SSO 可以设置 fdb.enable_mac",
            assertion=sql_fails("only sso can set configuration(fdb.enable_mac)"),
        ),
        sql_step(
            title="DBA 不能设置审计开关",
            user="postgres", sql="ALTER SYSTEM SET fdb.enable_audit = 1",
            expected="执行失败，错误信息说明只有 SAO 可以设置 fdb.enable_audit",
            assertion=sql_fails("only sao can set configuration(fdb.enable_audit)"),
        ),
    ],
    "teardown": "所有 ALTER SYSTEM 均预期失败，不修改运行配置",
}
