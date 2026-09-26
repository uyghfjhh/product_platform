from framework.assertions import output_contains_text, rows_equal
from framework.steps import sql_step


LOCAL_SUMMARY_SQL = """
SELECT count(*)::text,
       bool_and(nodestate = 'ACTIVE')::text,
       bool_and(real_nodestate = 'ACTIVE')::text,
       bool_and(is_abnormal = 'OK')::text,
       bool_and(detail = 'OK')::text
FROM fdd.show_node_info(false, false)
"""

ALL_SUMMARY_SQL = """
SELECT count(*)::text,
       bool_and(nodestate = 'ACTIVE')::text,
       bool_and(real_nodestate = 'ACTIVE')::text,
       bool_and(is_abnormal = 'OK')::text,
       bool_and(detail = 'OK')::text
FROM fdd.show_node_info(true, false)
"""


CASE = {
    "id": "mmr.cluster_verification.basic",
    "name": "多活集群校验 UDF 基础参数组合",
    "document": "多活功能测试文档.md",
    "section": "5.1.1",
    "group": "cluster_verification",
    "fixtures": ["cluster"],
    "requirements": {
        "plugins": ["fdd_mmr"], "groups": ["mmr"],
        "writable_node": True, "node": "mmr:mmr1",
    },
    "prerequisites": [
        "三成员多活集群已完成 join，三个成员主库均为 ACTIVE。",
        "本用例仅读取 fdd.show_node_info，不修改多活元数据或业务数据。",
    ],
    "steps": [
        sql_step(
            "成员 mmr1 本地校验并展示成功记录", "postgres",
            "SELECT nodeid,nodename,nodestate,real_nodestate,is_abnormal,detail "
            "FROM fdd.show_node_info(false, false)",
            "返回本成员的一行 ACTIVE/ACTIVE/OK/OK 校验结果",
            output_contains_text("ACTIVE", "OK"), node="mmr:mmr1"),
        sql_step(
            "成员 mmr1 本地校验结果精确断言", "postgres", LOCAL_SUMMARY_SQL,
            "返回 1|true|true|true|true", rows_equal([["1", "true", "true", "true", "true"]]),
            node="mmr:mmr1"),
        sql_step(
            "成员 mmr2 本地校验并展示成功记录", "postgres",
            "SELECT nodeid,nodename,nodestate,real_nodestate,is_abnormal,detail "
            "FROM fdd.show_node_info(false, false)",
            "返回本成员的一行 ACTIVE/ACTIVE/OK/OK 校验结果",
            output_contains_text("ACTIVE", "OK"), node="mmr:mmr2"),
        sql_step(
            "成员 mmr2 本地校验结果精确断言", "postgres", LOCAL_SUMMARY_SQL,
            "返回 1|true|true|true|true", rows_equal([["1", "true", "true", "true", "true"]]),
            node="mmr:mmr2"),
        sql_step(
            "成员 mmr3 本地校验并展示成功记录", "postgres",
            "SELECT nodeid,nodename,nodestate,real_nodestate,is_abnormal,detail "
            "FROM fdd.show_node_info(false, false)",
            "返回本成员的一行 ACTIVE/ACTIVE/OK/OK 校验结果",
            output_contains_text("ACTIVE", "OK"), node="mmr:mmr3"),
        sql_step(
            "成员 mmr3 本地校验结果精确断言", "postgres", LOCAL_SUMMARY_SQL,
            "返回 1|true|true|true|true", rows_equal([["1", "true", "true", "true", "true"]]),
            node="mmr:mmr3"),
        sql_step(
            "成员 mmr1 全集群校验并展示全部记录", "postgres",
            "SELECT nodeid,nodename,nodestate,real_nodestate,is_abnormal,detail "
            "FROM fdd.show_node_info(true, false) ORDER BY nodeid",
            "返回三个成员的 ACTIVE/ACTIVE/OK/OK 校验结果",
            output_contains_text("ACTIVE", "OK"), node="mmr:mmr1"),
        sql_step(
            "成员 mmr1 全集群校验结果精确断言", "postgres", ALL_SUMMARY_SQL,
            "返回 3|true|true|true|true", rows_equal([["3", "true", "true", "true", "true"]]),
            node="mmr:mmr1"),
        sql_step(
            "成员 mmr1 仅显示集群差异", "postgres",
            "SELECT nodeid,nodename,is_abnormal,detail "
            "FROM fdd.show_node_info(true, true) ORDER BY nodeid",
            "无校验异常，返回 0 行", rows_equal([]), node="mmr:mmr1"),
    ],
    "teardown": "只读校验，无需清理。",
}
