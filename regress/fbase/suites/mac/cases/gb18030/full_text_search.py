from framework.assertions import command_succeeds, rows_equal
from framework.steps import sql_step


DATABASE = "fbase_regress_gb18030_fts"
TABLE = "fbase_regress_gb18030_fts_table"


def gb_sql(title, sql, expected, assertion, continue_on_failure=False):
    step = sql_step(title, "postgres", sql, expected, assertion,
                    database=DATABASE, client_encoding="UTF8")
    if continue_on_failure:
        step["continue_on_failure"] = True
    return step


CASE = {
    "id": "mac.gb18030.full_text_search",
    "name": "GB18030 zhparser 全文索引与检索",
    "document": "gb18030.md",
    "section": "4.1-4.2",
    "group": "gb18030",
    "fixtures": ["cluster", {"type": "database", "setup": False, "name": DATABASE}],
    "requirements": {"plugins": ["fbase_mac"], "extensions": ["zhparser"],
                     "writable_node": True, "node": "primary"},
    "prerequisites": [
        "已按 4.1 安装 scws，并使用仓库 Third-party/zhparser-2.2 编译安装支持 GB18030 的 zhparser",
        "系统安装 zh_CN.gb18030 locale，template0 可用于创建 GB18030 数据库",
    ],
    "steps": [
        sql_step("创建 GB18030 全文检索数据库", "postgres",
                 "CREATE DATABASE %s TEMPLATE template0 ENCODING 'GB18030' LOCALE 'zh_CN.gb18030'" % DATABASE,
                 "返回 CREATE DATABASE", command_succeeds()),
        gb_sql("安装 zhparser 扩展", "CREATE EXTENSION zhparser",
               "返回 CREATE EXTENSION", command_succeeds()),
        gb_sql("创建 zhparser 文本搜索配置", "CREATE TEXT SEARCH CONFIGURATION zh (PARSER = zhparser)",
               "返回 CREATE TEXT SEARCH CONFIGURATION", command_succeeds()),
        gb_sql("配置 n/v/a/i/e/l 词类映射", "ALTER TEXT SEARCH CONFIGURATION zh ADD MAPPING FOR n, v, a, i, e, l WITH simple",
               "返回 ALTER TEXT SEARCH CONFIGURATION", command_succeeds()),
        gb_sql("创建文档定义的 udf_to_tsvector 函数",
               "CREATE FUNCTION udf_to_tsvector(regconfig, text) RETURNS tsvector AS $$ SELECT array_to_tsvector(array_agg(token)) FROM ts_debug($1, $2) WHERE (char_length(token) = 1 AND octet_length(token) <> 1) OR (char_length(token) = octet_length(token)) $$ LANGUAGE sql STRICT IMMUTABLE",
               "返回 CREATE FUNCTION", command_succeeds()),
        gb_sql("创建全文检索测试表", "CREATE TABLE %s(id serial, info text)" % TABLE,
               "返回 CREATE TABLE", command_succeeds()),
        gb_sql("插入中文第一段文本", "INSERT INTO %s(info) VALUES ('中文（chinese）是中国的语言文字。特指汉族的语言文字，即汉语和汉字')" % TABLE,
               "返回 INSERT 0 1", command_succeeds()),
        gb_sql("插入含方言的中文文本", "INSERT INTO %s(info) VALUES ('中文（汉语）有标准语和方言之分，其标准语即汉语普通话，是规范后的汉民族共同语，也是中国的国家通用语言。现代汉语方言一般可分为：官话方言、吴方言、湘方言、客家方言、闽方言、粤方言、赣方言等')" % TABLE,
               "返回 INSERT 0 1", command_succeeds()),
        gb_sql("插入历史中文文本", "INSERT INTO %s(info) VALUES ('相传黄帝时中原有万国，夏朝时还有三千国，周初分封八百诸侯，而五方之民，言语不通')" % TABLE,
               "返回 INSERT 0 1", command_succeeds()),
        gb_sql("插入英文文本", "INSERT INTO %s(info) VALUES ('Time goes by so fast, people go in and out of your life')" % TABLE,
               "返回 INSERT 0 1", command_succeeds()),
        gb_sql("创建 zhparser GIN 全文索引", "CREATE INDEX %s_idx ON %s USING gin(to_tsvector('zh', info))" % (TABLE, TABLE),
               "返回 CREATE INDEX", command_succeeds()),
        gb_sql("AND 组合检索中文和方言", "SELECT id::text FROM %s WHERE to_tsvector('zh', info) @@ to_tsquery('中文') AND to_tsvector('zh', info) @@ to_tsquery('方言')" % TABLE,
               "仅返回第二段文本的 ID 2", rows_equal([["2"]])),
        gb_sql("OR 组合检索中文和英文 people", "SELECT id::text FROM %s WHERE to_tsvector('zh', info) @@ to_tsquery('中文') OR udf_to_tsvector('zh', info) @@ to_tsquery('simple', 'people') ORDER BY id" % TABLE,
               "返回 ID 1、2、4", rows_equal([["1"], ["2"], ["4"]])),
        gb_sql("AND NOT 排除普通话文本", "SELECT id::text FROM %s WHERE to_tsvector('zh', info) @@ to_tsquery('中文') AND NOT to_tsvector('zh', info) @@ to_tsquery('普通话')" % TABLE,
               "仅返回第一段文本的 ID 1", rows_equal([["1"]])),
        gb_sql("删除全文检索测试表", "DROP TABLE %s" % TABLE,
               "返回 DROP TABLE", command_succeeds()),
    ],
    "teardown": "database fixture 删除 GB18030 全文检索测试数据库及其中的扩展、配置和函数。",
}
