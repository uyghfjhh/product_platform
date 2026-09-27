from framework.assertions import command_succeeds, output_contains_text, rows_equal
from framework.steps import sql_step


SEQUENCE = "fbase_regress_mmr_snowflake_{run_id}"
SEQUENCE_REF = "public.%s" % SEQUENCE


CASE = {
    "id": "mmr.global_sequence.snowflake_nextval",
    "name": "多活雪花序列 nextval 与格式化结果",
    "document": "多活功能测试文档.md",
    "section": "9.6.1.1",
    "group": "global_sequence",
    "fixtures": [
        "cluster",
        {"type": "sequence", "node": "mmr:mmr1", "name": SEQUENCE, "setup": False},
    ],
    "requirements": {
        "plugins": ["fdd_mmr"], "groups": ["mmr"],
        "writable_node": True, "node": "mmr:mmr1",
    },
    "evidence_nodes": ["mmr:mmr1"],
    "prerequisites": [
        "mmr1 已加入多活集群并分配有效 node_id。",
        "用例只创建 run_id 隔离的本地序列，不创建表，避免触发额外的复制集自动加入流程。",
    ],
    "steps": [
        sql_step(
            "按文档创建普通 PostgreSQL 序列", "postgres",
            "CREATE SEQUENCE %s START WITH 1 INCREMENT BY 1 NO MINVALUE NO MAXVALUE CACHE 1" %
            SEQUENCE_REF,
            "CREATE SEQUENCE 成功", command_succeeds(), node="mmr:mmr1"),
        sql_step(
            "两次调用 fdd.nextval 生成唯一雪花序列值", "postgres",
            "WITH generated AS (SELECT fdd.nextval('%s'::regclass) AS value "
            "FROM generate_series(1, 2)) "
            "SELECT count(*)::text,bool_and(value > 0)::text,"
            "(count(DISTINCT value) = 2)::text,"
            "bool_and((fdd.format(value)::json ->> 'id') = '1')::text FROM generated" %
            SEQUENCE_REF,
            "返回 2|true|true|true：两值均为正、彼此不同，格式化 node id 为 1",
            rows_equal([["2", "true", "true", "true"]]), node="mmr:mmr1"),
        sql_step(
            "展示雪花值及 fdd.format 的真实格式化结果", "postgres",
            "WITH generated AS (SELECT fdd.nextval('%s'::regclass) AS value) "
            "SELECT value,fdd.format(value) AS formatted FROM generated" % SEQUENCE_REF,
            "返回正整数 id 及包含 JSON 字段 id=1 的格式化结果",
            output_contains_text('"id": 1'), node="mmr:mmr1"),
        sql_step(
            "删除雪花序列测试对象", "postgres",
            "DROP SEQUENCE IF EXISTS %s" % SEQUENCE_REF,
            "DROP SEQUENCE 成功", command_succeeds(), node="mmr:mmr1"),
    ],
    "teardown": "显式删除测试序列；sequence fixture 在任何失败路径重复执行 DROP SEQUENCE IF EXISTS。",
}
