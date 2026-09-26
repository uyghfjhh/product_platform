from framework.assertions import command_succeeds, output_contains, sql_fails
from framework.steps import sql_step
from suites.mac.cases.separation_of_duties.common import (
    COMMON_PREREQUISITES, separation_requirements,
)


CASE = {
    "id": "mac.separation_of_duties.dba_mac_policy_table_protection",
    "name": "MAC 策略表及策略列保护",
    "document": "三权分立功能转测.md",
    "section": "5.3.4.9-5.3.4.10",
    "group": "separation_of_duties",
    "fixtures": [
        "cluster",
        {
            "type": "settings", "user": "sso",
            "values": {"fdb.enable_mac": "on"}, "apply": "reload",
            "purpose": "启用强制访问控制，验证受策略保护表和策略列限制",
        },
        {"type": "mac_policy", "table": "fbase_regress_mac_guard",
         "policy": "fbase_regress_mac_policy",
         "column": "fbase_regress_mac_label"},
    ],
    "requirements": separation_requirements(["sso"]),
    "prerequisites": COMMON_PREREQUISITES + [
        "SSO 有权设置 fdb.enable_mac 并管理 MAC 策略",
    ],
    "steps": [
        sql_step(
            title="MAC 开启时 DBA 不能删除应用策略的表",
            user="postgres", sql="DROP TABLE fbase_regress_mac_guard",
            expected="执行失败，错误信息说明表已应用 MAC 策略",
            assertion=sql_fails("can't drop table has mac access"),
        ),
        sql_step(
            title="DBA 不能删除 MAC 策略列",
            user="postgres",
            sql='ALTER TABLE fbase_regress_mac_guard DROP COLUMN "fbase_regress_mac_label"',
            expected="执行失败，错误信息说明不能删除 MAC 策略列",
            assertion=sql_fails("can't drop mac policy column of table"),
        ),
        sql_step(
            title="DBA 不能重置 MAC 策略列默认值",
            user="postgres",
            sql='ALTER TABLE fbase_regress_mac_guard ALTER COLUMN "fbase_regress_mac_label" DROP DEFAULT',
            expected="执行失败，错误信息说明不能修改 MAC 策略列默认值",
            assertion=sql_fails("can't change mac policy column default value of table"),
        ),
        sql_step(
            title="SSO 关闭 MAC 开关以验证表可删除",
            user="sso", sql="ALTER SYSTEM SET fdb.enable_mac = off",
            expected="执行成功，等待 reload 后验证关闭状态",
            assertion=output_contains("ALTER SYSTEM"),
        ),
        {
            "type": "cluster_action", "title": "重载 MAC 开关",
            "action": "reload", "expected": "reload 执行成功",
            "assertion": command_succeeds(),
        },
        sql_step(
            title="确认 MAC 已关闭", user="postgres", sql="SHOW fdb.enable_mac",
            expected="返回 off", assertion=output_contains("off"),
        ),
        sql_step(
            title="MAC 关闭时 DBA 可以删除应用策略的表",
            user="postgres", sql="DROP TABLE fbase_regress_mac_guard",
            expected="执行成功，返回 DROP TABLE", assertion=output_contains("DROP TABLE"),
        ),
    ],
    "teardown": "settings fixture 无条件恢复 fdb.enable_mac；mac_policy fixture 随后清理策略和测试表",
}
