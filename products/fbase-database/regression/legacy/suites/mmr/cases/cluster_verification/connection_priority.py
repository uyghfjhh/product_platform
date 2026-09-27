from framework.assertions import command_succeeds, rows_equal
from framework.steps import sql_step, wait_sql_step


SOURCE = "mmr:mmr1"
TARGET = "mmr:mmr2"


CASE = {
    "id": "mmr.cluster_verification.connection_failure_priority",
    "name": "多活集群校验连接失败优先级",
    "document": "多活功能测试文档.md",
    "section": "5.1.2",
    "group": "cluster_verification",
    "fixtures": ["cluster", {"type": "node_running_guard", "node": TARGET}],
    "requirements": {
        "clusters": ["mmr"], "plugins": ["fdd_mmr"], "groups": ["mmr"],
        "writable_node": True, "node": SOURCE,
    },
    "evidence_nodes": [SOURCE, TARGET],
    "prerequisites": [
        "隔离两成员多活集群处于 healthy，两个成员均为 ACTIVE。",
        "仅短暂停止 mmr2；node_running_guard 在任何失败路径启动该成员。",
    ],
    "steps": [
        sql_step(
            "确认连接失败前集群校验健康", "postgres",
            "SELECT (count(*) >= 2)::text || '|' || bool_and(is_abnormal='OK')::text "
            "FROM fdd.show_node_info(true,false)",
            "返回 true|true", rows_equal([["true|true"]]), node=SOURCE),
        {
            "type": "node_action", "title": "按文档停止被校验的 active 成员",
            "node": TARGET, "action": "stop_immediate", "expected": "立即停止成功",
            "assertion": command_succeeds(),
        },
        sql_step(
            "校验连接失败阻断低优先级检查", "postgres",
            "WITH result AS (SELECT is_abnormal FROM fdd.show_node_info(true,false)) "
            "SELECT (count(*) FILTER (WHERE is_abnormal='CONNECT_FAIL') > 0)::text,"
            "(count(*) FILTER (WHERE is_abnormal NOT IN ('OK','CONNECT_FAIL')) = 0)::text "
            "FROM result",
            "返回 true|true：至少一条 CONNECT_FAIL，且没有低优先级异常",
            rows_equal([["true", "true"]]), node=SOURCE),
        {
            "type": "node_action", "title": "恢复被停止的成员",
            "node": TARGET, "action": "start", "expected": "启动成功",
            "assertion": command_succeeds(),
        },
        wait_sql_step(
            "确认恢复后集群校验健康", "postgres",
            "SELECT (count(*) >= 2)::text || '|' || bool_and(is_abnormal='OK')::text "
            "FROM fdd.show_node_info(true,false)",
            "返回 true|true", rows_equal([["true|true"]]), node=SOURCE, timeout=20),
    ],
    "teardown": "显式启动 mmr2；node_running_guard 在所有异常路径重复确认成员已启动。",
}
