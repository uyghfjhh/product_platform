from framework.assertions import sql_fails
from framework.steps import sql_step
from suites.mac.cases.separation_of_duties.common import (
    COMMON_PREREQUISITES, separation_requirements,
)


CASE = {
    "id": "mac.separation_of_duties.dba_administrator_owner_protection",
    "name": "DBA 禁止将对象所有者设为 SSO 或 SAO",
    "document": "三权分立功能转测.md",
    "section": "5.3.4.11",
    "group": "separation_of_duties",
    "fixtures": ["cluster", "roles"],
    "requirements": separation_requirements(["sso", "sao"]),
    "prerequisites": COMMON_PREREQUISITES + ["数据库初始化角色 sso、sao 存在"],
    "steps": [
        sql_step(
            title="DBA 不能将临时表所有者设为 SSO",
            user="postgres",
            sql=("BEGIN; CREATE TABLE fbase_regress_owner_guard(id integer); "
                 "ALTER TABLE fbase_regress_owner_guard OWNER TO sso; ROLLBACK"),
            expected="执行失败，错误信息说明 sso 和 sao 不能成为对象所有者",
            assertion=sql_fails("sso and sao cannot be the owner"),
        ),
        sql_step(
            title="DBA 不能将临时表所有者设为 SAO",
            user="postgres",
            sql=("BEGIN; CREATE TABLE fbase_regress_owner_guard(id integer); "
                 "ALTER TABLE fbase_regress_owner_guard OWNER TO sao; ROLLBACK"),
            expected="执行失败，错误信息说明 sso 和 sao 不能成为对象所有者",
            assertion=sql_fails("sso and sao cannot be the owner"),
        ),
        sql_step(
            title="DBA 不能将现有对象批量重新指派给 SSO",
            user="postgres",
            sql="REASSIGN OWNED BY postgres TO sso",
            expected="执行失败，错误信息说明 sso 和 sao 不能作为重新指派目标",
            assertion=sql_fails("sso and sao can not be reassign target"),
        ),
    ],
    "teardown": "临时表创建和 OWNER 修改均在事务内；失败连接结束后不保留对象",
}
