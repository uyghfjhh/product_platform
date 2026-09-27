from framework.assertions import sql_fails
from framework.steps import sql_step
from suites.mac.cases.separation_of_duties.common import (
    COMMON_PREREQUISITES, separation_requirements,
)


CASE = {
    "id": "mac.separation_of_duties.dba_metadata_function_restrictions",
    "name": "DBA 禁止修改等保元数据函数",
    "document": "三权分立功能转测.md",
    "section": "5.3.4.7",
    "group": "separation_of_duties",
    "fixtures": ["cluster"],
    "requirements": separation_requirements(),
    "prerequisites": COMMON_PREREQUISITES + ["fdb_mac.create_policy 安全函数存在"],
    "steps": [
        sql_step(
            title="DBA 不能重命名强制访问控制函数",
            user="postgres",
            sql=("BEGIN; ALTER FUNCTION fdb_mac.create_policy(name, name, boolean, boolean) "
                 "RENAME TO fbase_regress_create_policy; ROLLBACK"),
            expected="执行失败，错误信息说明不能修改 MAC 函数属性",
            assertion=sql_fails("change mac function's attributes"),
        ),
        sql_step(
            title="DBA 不能修改强制访问控制函数所有者",
            user="postgres",
            sql=("BEGIN; ALTER FUNCTION fdb_mac.create_policy(name, name, boolean, boolean) "
                 "OWNER TO postgres; ROLLBACK"),
            expected="执行失败，错误信息说明不能修改 MAC 函数属性",
            assertion=sql_fails("change mac function's attributes"),
        ),
    ],
    "teardown": "所有函数 ALTER 均在事务内；预期失败，不保留函数属性修改",
}
