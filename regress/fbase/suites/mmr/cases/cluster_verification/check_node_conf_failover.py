from framework.assertions import command_succeeds, output_contains_text, rows_equal
from framework.steps import sql_step


HEALTHY_CLUSTER_SQL = """
SELECT count(*)::text,
       bool_and(nodestate = 'ACTIVE')::text,
       bool_and(real_nodestate = 'ACTIVE')::text,
       bool_and(is_abnormal = 'OK')::text,
       bool_and(detail = 'OK')::text
FROM fdd.show_node_info(true, false)
"""


CASE = {
    "id": "mmr.cluster_verification.check_node_conf_failover_exclusion",
    "name": "多活集群校验配置表跳过 failover 一致性校验",
    "document": "多活功能测试文档.md",
    "section": "5.2 测试二",
    "group": "cluster_verification",
    "fixtures": [
        "cluster",
        {"type": "mmr_check_node_conf_empty", "nodes": ["mmr:mmr1"]},
        {"type": "mmr_node_failover_guard", "node": "mmr:mmr1",
         "node_name": "mmr1", "expected_state": "true"},
    ],
    "requirements": {
        "plugins": ["fdd_mmr"], "groups": ["mmr"],
        "writable_node": True, "node": "mmr:mmr1",
    },
    "evidence_nodes": ["mmr:mmr1", "mmr:mmr2", "mmr:mmr3"],
    "prerequisites": [
        "三成员多活集群已完成 join，三个成员主库均为 ACTIVE。",
        "mmr1 本地 fdd.mmr_node 中 mmr1 的 failover 为 true。",
        "mmr1 的 fdd.mmr_check_node_conf 为空；fixture 保存 failover 原值并在任何失败路径恢复。",
    ],
    "steps": [
        sql_step(
            "确认初始全集群校验健康", "postgres", HEALTHY_CLUSTER_SQL,
            "返回 3|true|true|true|true",
            rows_equal([["3", "true", "true", "true", "true"]]), node="mmr:mmr1"),
        sql_step(
            "确认 mmr1 本地校验配置表为空", "postgres",
            "SELECT local_exclude_tables::text,failover_global_same::text,dsn_global_same::text "
            "FROM fdd.mmr_check_node_conf",
            "返回 0 行", rows_equal([]), node="mmr:mmr1"),
        sql_step(
            "确认 mmr1 本地记录的自身 failover 为 true", "postgres",
            "SELECT node_name::text,failover::text FROM fdd.mmr_node WHERE node_name = 'mmr1'",
            "返回 mmr1|true", rows_equal([["mmr1", "true"]]), node="mmr:mmr1"),
        sql_step(
            "仅在 mmr1 将自身 failover 改为 false", "postgres",
            "SELECT fdd.alter_node_failover('mmr1', false, false)",
            "SQL 执行成功", command_succeeds(), node="mmr:mmr1"),
        sql_step(
            "确认 mmr1 的本地 failover 已变为 false", "postgres",
            "SELECT node_name::text,failover::text FROM fdd.mmr_node WHERE node_name = 'mmr1'",
            "返回 mmr1|false", rows_equal([["mmr1", "false"]]), node="mmr:mmr1"),
        sql_step(
            "展示 failover 不一致导致的元数据校验错误", "postgres",
            "SELECT nodeid,nodename,is_abnormal,detail FROM fdd.show_node_info(true, false) "
            "WHERE is_abnormal = 'DIFF_ClUSTER' AND detail = '元数据不一致(mmr_node)'",
            "显示 DIFF_ClUSTER 和元数据不一致(mmr_node)",
            output_contains_text("DIFF_ClUSTER", "元数据不一致(mmr_node)"), node="mmr:mmr1"),
        sql_step(
            "精确确认存在 failover 触发的元数据不一致", "postgres",
            "SELECT EXISTS (SELECT 1 FROM fdd.show_node_info(true, false) "
            "WHERE is_abnormal = 'DIFF_ClUSTER' "
            "AND detail = '元数据不一致(mmr_node)')::text",
            "返回 true", rows_equal([["true"]]), node="mmr:mmr1"),
        sql_step(
            "设置 failover_global_same 为 false 跳过该项校验", "postgres",
            "SELECT fdd.alter_mmr_check_node_conf(NULL, false, true)",
            "返回 t", rows_equal([["t"]]), node="mmr:mmr1"),
        sql_step(
            "确认 mmr1 已记录 failover 跳过配置", "postgres",
            "SELECT COALESCE(local_exclude_tables::text, '<NULL>'), "
            "failover_global_same::text,dsn_global_same::text "
            "FROM fdd.mmr_check_node_conf",
            "返回 <NULL>|false|true",
            rows_equal([["<NULL>", "false", "true"]]), node="mmr:mmr1"),
        sql_step(
            "确认跳过 failover 校验后全集群重新健康", "postgres", HEALTHY_CLUSTER_SQL,
            "返回 3|true|true|true|true",
            rows_equal([["3", "true", "true", "true", "true"]]), node="mmr:mmr1"),
        sql_step(
            "恢复 mmr1 本地自身 failover 为 true", "postgres",
            "SELECT fdd.alter_node_failover('mmr1', true, false)",
            "SQL 执行成功", command_succeeds(), node="mmr:mmr1"),
        sql_step(
            "清空 mmr1 的临时集群校验配置", "postgres",
            "TRUNCATE TABLE fdd.mmr_check_node_conf",
            "TRUNCATE 成功", command_succeeds(), node="mmr:mmr1"),
        sql_step(
            "确认恢复后全集群校验保持健康", "postgres", HEALTHY_CLUSTER_SQL,
            "返回 3|true|true|true|true",
            rows_equal([["3", "true", "true", "true", "true"]]), node="mmr:mmr1"),
    ],
    "teardown": "显式恢复 mmr1 的 failover 并清空临时配置；fixture 保存原值并在任何失败路径重复恢复。",
}
