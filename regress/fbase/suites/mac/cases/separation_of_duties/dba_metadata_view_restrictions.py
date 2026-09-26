from framework.assertions import sql_fails
from framework.steps import sql_step
from suites.mac.cases.separation_of_duties.common import (
    COMMON_PREREQUISITES, separation_requirements,
)


CASE = {
    "id": "mac.separation_of_duties.dba_metadata_view_restrictions",
    "name": "DBA 禁止修改等保元数据视图",
    "document": "三权分立功能转测.md",
    "section": "5.3.4.6",
    "group": "separation_of_duties",
    "fixtures": ["cluster"],
    "requirements": separation_requirements(),
    "prerequisites": COMMON_PREREQUISITES + ["fdb_mac.level_v 元数据视图存在"],
    "steps": [
        sql_step(
            title="DBA 不能重命名强制访问控制元数据视图",
            user="postgres",
            sql=("BEGIN; ALTER VIEW fdb_mac.level_v RENAME TO "
                 "fbase_regress_level_v; ROLLBACK"),
            expected="执行失败，错误信息说明不能重命名 MAC 元数据关系",
            assertion=sql_fails("rename metadata relation(level_v)"),
        ),
        sql_step(
            title="DBA 不能修改强制访问控制元数据视图所有者",
            user="postgres",
            sql="BEGIN; ALTER VIEW fdb_mac.level_v OWNER TO postgres; ROLLBACK",
            expected="执行失败，错误信息说明不能修改 MAC 元数据视图属性",
            assertion=sql_fails("metadata table(level_v)'s attributes"),
        ),
    ],
    "teardown": "所有视图 ALTER 均在事务内；预期失败，不保留元数据修改",
}
