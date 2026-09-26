from framework.assertions import command_succeeds, output_contains_text, rows_equal
from framework.steps import sql_step


# A bigserial sequence appends _id_seq.  Keep the full generated identifier
# below PostgreSQL's 63-byte limit so the documented regclass resolves exactly.
TABLE = "fbase_r_mmr_sn_{run_id}"
TABLE_REF = "public.%s" % TABLE
SEQUENCE = "%s_id_seq" % TABLE
SEQUENCE_REF = "public.%s" % SEQUENCE


CASE = {
    "id": "mmr.global_sequence.snowflake_conversion",
    "name": "普通序列转换及恢复雪花序列默认值",
    "document": "多活功能测试文档.md",
    "section": "9.6.1.2",
    "group": "global_sequence",
    "fixtures": [
        "cluster",
        {"type": "table", "node": "mmr:mmr1", "name": TABLE, "setup": False},
        {"type": "mmr_async_set_mode_recovery",
         "nodes": ["mmr:mmr1", "mmr:mmr2", "mmr:mmr3"]},
    ],
    "requirements": {
        "plugins": ["fdd_mmr"], "groups": ["mmr"],
        "writable_node": True, "node": "mmr:mmr1",
    },
    "evidence_nodes": ["mmr:mmr1"],
    "prerequisites": [
        "mmr1 已加入多活集群并具有有效 node_id。",
        "默认复制集不自动添加新表；测试表只在 mmr1 创建。建表会使本地 set_mode 进入 n，"
        "fixture 会在删除表后调用 fdd.check_async_record() 恢复三个成员。",
    ],
    "steps": [
        sql_step(
            "按文档创建使用普通 bigserial 序列的测试表", "postgres",
            "CREATE TABLE %s (id bigserial PRIMARY KEY, some_data text)" % TABLE_REF,
            "CREATE TABLE 成功", command_succeeds(), node="mmr:mmr1"),
        sql_step(
            "按文档插入两行普通序列数据", "postgres",
            "INSERT INTO %s(some_data) VALUES ('first row'), ('second row')" % TABLE_REF,
            "INSERT 0 2 成功", command_succeeds(), node="mmr:mmr1"),
        sql_step(
            "确认转换前默认值为 PostgreSQL nextval 且序列值为 1、2", "postgres",
            "SELECT (pg_get_expr(adbin, adrelid) LIKE 'nextval(%%')::text,"
            "count(*)::text,min(id)::text,max(id)::text FROM pg_attrdef "
            "CROSS JOIN %s WHERE adrelid = '%s'::regclass AND adnum = 1 "
            "GROUP BY adbin,adrelid" % (TABLE_REF, TABLE_REF),
            "返回 true|2|1|2", rows_equal([["true", "2", "1", "2"]]), node="mmr:mmr1"),
        sql_step(
            "按文档将普通依赖序列转换为雪花序列默认值", "postgres",
            "SELECT fdd.convert_sequence_to_fdd('%s'::regclass)::text" % SEQUENCE_REF,
            "返回 2：主键列和关联定义已转换", rows_equal([["2"]]), node="mmr:mmr1"),
        sql_step(
            "展示转换后的列默认值", "postgres",
            "SELECT pg_get_expr(adbin, adrelid) FROM pg_attrdef "
            "WHERE adrelid = '%s'::regclass AND adnum = 1" % TABLE_REF,
            "默认值包含 fdd.nextval 和测试序列名",
            output_contains_text("fdd.nextval", SEQUENCE), node="mmr:mmr1"),
        sql_step(
            "按文档插入两行雪花序列数据", "postgres",
            "INSERT INTO %s(some_data) VALUES ('snowflake row 1'), ('snowflake row 2')" % TABLE_REF,
            "INSERT 0 2 成功", command_succeeds(), node="mmr:mmr1"),
        sql_step(
            "确认两行雪花值均为正、互不相同且格式化 node id 为 1", "postgres",
            "WITH generated AS (SELECT id FROM %s WHERE some_data LIKE 'snowflake%%') "
            "SELECT count(*)::text,bool_and(id > 2)::text,"
            "(count(DISTINCT id) = 2)::text,"
            "bool_and((fdd.format(id)::json ->> 'id') = '1')::text FROM generated" % TABLE_REF,
            "返回 2|true|true|true", rows_equal([["2", "true", "true", "true"]]),
            node="mmr:mmr1"),
        sql_step(
            "展示雪花值、格式化结果和业务数据", "postgres",
            "SELECT id,fdd.format(id),some_data FROM %s "
            "WHERE some_data LIKE 'snowflake%%' ORDER BY id" % TABLE_REF,
            "返回两行雪花值，格式化结果包含 JSON 字段 id=1",
            output_contains_text('"id": 1', "snowflake row 1", "snowflake row 2"), node="mmr:mmr1"),
        sql_step(
            "按文档将列默认值恢复为 PostgreSQL nextval", "postgres",
            "ALTER TABLE %s ALTER COLUMN id SET DEFAULT nextval('%s'::regclass)" %
            (TABLE_REF, SEQUENCE_REF),
            "ALTER TABLE 成功", command_succeeds(), node="mmr:mmr1"),
        sql_step(
            "确认回转后的默认值为 PostgreSQL nextval", "postgres",
            "SELECT (pg_get_expr(adbin, adrelid) LIKE 'nextval(%%')::text "
            "FROM pg_attrdef WHERE adrelid = '%s'::regclass AND adnum = 1" % TABLE_REF,
            "返回 true", rows_equal([["true"]]), node="mmr:mmr1"),
        sql_step(
            "删除雪花转换测试表", "postgres",
            "DROP TABLE IF EXISTS %s" % TABLE_REF,
            "DROP TABLE 成功", command_succeeds(), node="mmr:mmr1"),
    ],
    "teardown": "显式删除测试表；table fixture 在任何失败路径删除表和 bigserial 关联序列，"
                "随后 mmr_async_set_mode_recovery 调用产品 UDF 恢复 set_mode。",
}
