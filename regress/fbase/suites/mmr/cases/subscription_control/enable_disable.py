from framework.assertions import command_succeeds, rows_equal
from framework.steps import sql_step


TARGET_SUBSCRIPTION = "fmmr_postgres_fbase_regress_mmr_mmr2"


def state_step(title, metadata_enabled, runtime_enabled):
    return sql_step(
        title, "postgres",
        "SELECT (SELECT sub_enabled::text FROM fdd.mmr_subscription "
        "WHERE sub_name = '%s'),"
        "(SELECT subenabled::text FROM pg_subscription "
        "WHERE subname = '%s')" % (TARGET_SUBSCRIPTION, TARGET_SUBSCRIPTION),
        "返回 %s|%s" % (metadata_enabled, runtime_enabled),
        rows_equal([[metadata_enabled, runtime_enabled]]), node="mmr:mmr1")


def all_enabled_step(title, expected):
    return sql_step(
        title, "postgres",
        "SELECT (SELECT count(*)::text || '|' || bool_and(sub_enabled = %s)::text "
        "FROM fdd.mmr_subscription WHERE sub_name LIKE 'fmmr_%%'),"
        "(SELECT count(*)::text || '|' || bool_and(subenabled = %s)::text "
        "FROM pg_subscription WHERE subname LIKE 'fmmr_%%')" % (expected, expected),
        "返回 2|true|2|true", rows_equal([["2|true", "2|true"]]), node="mmr:mmr1")


CASE = {
    "id": "mmr.subscription_control.enable_disable",
    "name": "多活订阅端指定及全量启停",
    "document": "多活功能测试文档.md",
    "section": "8.1,8.2",
    "group": "subscription_control",
    "fixtures": [
        "cluster",
        {"type": "mmr_subscriptions_enabled_guard", "node": "mmr:mmr1", "expected_count": 2},
    ],
    "requirements": {
        "plugins": ["fdd_mmr"], "groups": ["mmr"],
        "writable_node": True, "node": "mmr:mmr1",
    },
    "evidence_nodes": ["mmr:mmr1"],
    "prerequisites": [
        "mmr1 已加入三成员多活集群，且本节点两个 fmmr_ 订阅均处于 enabled。",
        "fixture 在任意失败路径调用 fdd.alter_subscription_enable() 恢复本节点全部多活订阅。",
    ],
    "steps": [
        all_enabled_step("确认初始全部多活订阅在元数据和 pg_subscription 中均为 enabled", "true"),
        sql_step(
            "按文档对已启用的指定订阅重复执行 enable", "postgres",
            "SELECT fdd.alter_subscription_enable('%s')" % TARGET_SUBSCRIPTION,
            "SQL 执行成功且状态保持 enabled", command_succeeds(), node="mmr:mmr1"),
        state_step("确认重复 enable 后指定订阅保持 enabled", "true", "true"),
        sql_step(
            "按文档关闭指定订阅", "postgres",
            "SELECT fdd.alter_subscription_disable('%s')" % TARGET_SUBSCRIPTION,
            "SQL 执行成功", command_succeeds(), node="mmr:mmr1"),
        state_step("确认指定订阅在元数据和 pg_subscription 中均已 disabled", "false", "false"),
        sql_step(
            "按文档对已关闭的指定订阅重复执行 disable", "postgres",
            "SELECT fdd.alter_subscription_disable('%s')" % TARGET_SUBSCRIPTION,
            "SQL 执行成功且状态保持 disabled", command_succeeds(), node="mmr:mmr1"),
        state_step("确认重复 disable 后指定订阅保持 disabled", "false", "false"),
        sql_step(
            "按文档启用指定订阅", "postgres",
            "SELECT fdd.alter_subscription_enable('%s')" % TARGET_SUBSCRIPTION,
            "SQL 执行成功", command_succeeds(), node="mmr:mmr1"),
        state_step("确认指定订阅在元数据和 pg_subscription 中均已 enabled", "true", "true"),
        sql_step(
            "按文档关闭本节点全部多活订阅", "postgres",
            "SELECT fdd.alter_subscription_disable()",
            "SQL 执行成功", command_succeeds(), node="mmr:mmr1"),
        all_enabled_step("确认全部多活订阅均为 disabled", "false"),
        sql_step(
            "按文档对全部已关闭订阅重复执行 disable", "postgres",
            "SELECT fdd.alter_subscription_disable()",
            "SQL 执行成功且状态保持 disabled", command_succeeds(), node="mmr:mmr1"),
        sql_step(
            "按文档启用本节点全部多活订阅", "postgres",
            "SELECT fdd.alter_subscription_enable()",
            "SQL 执行成功", command_succeeds(), node="mmr:mmr1"),
        all_enabled_step("确认全部多活订阅均已 enabled", "true"),
        sql_step(
            "按文档对全部已启用订阅重复执行 enable", "postgres",
            "SELECT fdd.alter_subscription_enable()",
            "SQL 执行成功且状态保持 enabled", command_succeeds(), node="mmr:mmr1"),
        all_enabled_step("确认最终全部多活订阅仍为 enabled", "true"),
    ],
    "teardown": "用例显式恢复全部订阅；fixture 在任何失败路径再次调用产品 UDF 恢复元数据和 pg_subscription 状态。",
}
