from framework.assertions import sql_error, sql_fails
from framework.steps import sql_step
from suites.mac.cases.separation_of_duties.common import (
    COMMON_PREREQUISITES, separation_requirements,
)


CASE = {
    "id": "mac.separation_of_duties.dba_metadata_access_restrictions",
    "name": "DBA 禁止访问和修改等保元数据",
    "document": "三权分立功能转测.md",
    "section": "5.3.4.2-5.3.4.3",
    "group": "separation_of_duties",
    "fixtures": ["cluster"],
    "requirements": separation_requirements(),
    "prerequisites": COMMON_PREREQUISITES + [
        "fdb_mac.policy 安全元数据表和 fdb_audit.audit_rule 审计元数据表存在",
    ],
    "steps": [
        sql_step(
            title="DBA 不能读取强制访问控制元数据表",
            user="postgres", sql="SELECT * FROM fdb_mac.policy",
            expected="执行失败，SQLSTATE=42501，错误信息包含 permission denied",
            assertion=sql_error("42501", "permission denied"),
        ),
        sql_step(
            title="DBA 不能读取审计元数据表",
            user="postgres", sql="SELECT * FROM fdb_audit.audit_rule",
            expected="执行失败，SQLSTATE=42501，错误信息包含 permission denied",
            assertion=sql_error("42501", "permission denied"),
        ),
        sql_step(
            title="DBA 不能重命名强制访问控制元数据表",
            user="postgres",
            sql="BEGIN; ALTER TABLE fdb_mac.policy RENAME TO fbase_regress_policy; ROLLBACK",
            expected="执行失败，错误信息说明不能重命名 MAC 元数据关系",
            assertion=sql_fails("rename metadata relation"),
        ),
        sql_step(
            title="DBA 不能重命名审计元数据表",
            user="postgres",
            sql=("BEGIN; ALTER TABLE fdb_audit.audit_rule RENAME TO "
                 "fbase_regress_audit_rule; ROLLBACK"),
            expected="执行失败，错误信息说明不能重命名 MAC 保护的审计元数据关系",
            assertion=sql_fails("rename metadata relation(audit_rule)"),
        ),
    ],
    "teardown": "ALTER TABLE 在事务内执行；意外成功时显式回滚，失败时由连接结束自动回滚",
}
