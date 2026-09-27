from framework.assertions import sql_fails
from framework.steps import sql_step
from suites.mac.cases.separation_of_duties.common import (
    COMMON_PREREQUISITES, separation_requirements,
)


CASE = {
    "id": "mac.separation_of_duties.dba_metadata_index_restrictions",
    "name": "DBA 禁止修改等保元数据索引",
    "document": "三权分立功能转测.md",
    "section": "5.3.4.4",
    "group": "separation_of_duties",
    "fixtures": ["cluster"],
    "requirements": separation_requirements(),
    "prerequisites": COMMON_PREREQUISITES + ["fdb_mac.policy_pkey 元数据索引存在"],
    "steps": [
        sql_step(
            title="DBA 不能重命名强制访问控制元数据索引",
            user="postgres",
            sql=("BEGIN; ALTER INDEX fdb_mac.policy_pkey RENAME TO "
                 "fbase_regress_policy_pkey; ROLLBACK"),
            expected="执行失败，错误信息说明不能重命名 MAC 元数据索引",
            assertion=sql_fails("rename metadata index"),
        ),
        sql_step(
            title="DBA 不能修改强制访问控制元数据索引所有者",
            user="postgres",
            sql="BEGIN; ALTER INDEX fdb_mac.policy_pkey OWNER TO postgres; ROLLBACK",
            expected="执行失败，错误信息说明不能修改 MAC 元数据索引属性",
            assertion=sql_fails("metadata table(policy_pkey)'s attributes"),
        ),
    ],
    "teardown": "所有索引 ALTER 均在事务内；预期失败，不保留元数据修改",
}
