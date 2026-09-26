from framework.assertions import sql_fails
from framework.steps import sql_step
from suites.mac.cases.separation_of_duties.common import (
    COMMON_PREREQUISITES, separation_requirements,
)


CASE = {
    "id": "mac.separation_of_duties.dba_metadata_sequence_restrictions",
    "name": "DBA 禁止修改等保元数据序列",
    "document": "三权分立功能转测.md",
    "section": "5.3.4.5",
    "group": "separation_of_duties",
    "fixtures": ["cluster"],
    "requirements": separation_requirements(),
    "prerequisites": COMMON_PREREQUISITES + ["fdb_mac.policy_polid_seq 元数据序列存在"],
    "steps": [
        sql_step(
            title="DBA 不能修改强制访问控制元数据序列缓存",
            user="postgres",
            sql="BEGIN; ALTER SEQUENCE fdb_mac.policy_polid_seq CACHE 10; ROLLBACK",
            expected="执行失败，错误信息说明不能修改 MAC 元数据序列属性",
            assertion=sql_fails("change metadata sequence's attributes"),
        ),
        sql_step(
            title="DBA 不能重命名强制访问控制元数据序列",
            user="postgres",
            sql=("BEGIN; ALTER SEQUENCE fdb_mac.policy_polid_seq RENAME TO "
                 "fbase_regress_policy_seq; ROLLBACK"),
            expected="执行失败，错误信息说明不能重命名 MAC 元数据序列",
            assertion=sql_fails("rename metadata sequence"),
        ),
    ],
    "teardown": "所有序列 ALTER 均在事务内；预期失败，不保留元数据修改",
}
