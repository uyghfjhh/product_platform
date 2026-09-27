from framework.assertions import sql_fails
from framework.steps import sql_step
from suites.mac.cases.separation_of_duties.common import (
    COMMON_PREREQUISITES, separation_requirements,
)


CASE = {
    "id": "mac.separation_of_duties.sao_object_creation_restrictions",
    "name": "SAO 禁止创建数据库对象",
    "document": "三权分立功能转测.md",
    "section": "5.2.2",
    "group": "separation_of_duties",
    "fixtures": ["cluster", "roles"],
    "requirements": separation_requirements(["sao"]),
    "prerequisites": COMMON_PREREQUISITES + ["数据库初始化角色 sao 存在"],
    "steps": [
        sql_step(
            title="SAO 不能创建表",
            user="sao",
            sql="BEGIN; CREATE TABLE fbase_regress_sao_table(id integer); ROLLBACK",
            expected="执行失败，错误信息包含 permission denied",
            assertion=sql_fails("permission denied"),
        ),
        sql_step(
            title="SAO 不能创建模式",
            user="sao",
            sql="BEGIN; CREATE SCHEMA fbase_regress_sao_schema; ROLLBACK",
            expected="执行失败，错误信息包含 permission denied",
            assertion=sql_fails("permission denied"),
        ),
        sql_step(
            title="SAO 不能分配审计元数据表权限",
            user="sao",
            sql="BEGIN; GRANT SELECT ON fdb_audit.audit_rule TO PUBLIC; ROLLBACK",
            expected="执行失败，错误信息说明禁止修改审计元数据对象权限",
            assertion=sql_fails("cannot grant or revoke"),
        ),
    ],
    "teardown": "DDL 和 GRANT 均在事务内执行；意外成功时显式回滚，失败时由连接结束自动回滚",
}
