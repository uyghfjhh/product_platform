from framework.assertions import command_succeeds, rows_equal
from framework.steps import sql_step


DATABASE = "fbase_regress_gb18030"
TABLE = "計算機用語"


def gb_sql(title, sql, expected, assertion):
    """The case source is UTF-8 while the database is GB18030."""
    return sql_step(title, "postgres", sql, expected, assertion,
                    database=DATABASE, client_encoding="UTF8")


CASE = {
    "id": "mac.gb18030.database_encoding_and_sql",
    "name": "GB18030 数据库创建、内部编码及 SQL",
    "document": "gb18030.md",
    "section": "一,二,三",
    "group": "gb18030",
    "fixtures": ["cluster", {"type": "database", "setup": False, "name": DATABASE}],
    "requirements": {"writable_node": True, "node": "primary"},
    "prerequisites": ["系统安装 zh_CN.gb18030 locale，template0 可用于创建 GB18030 数据库"],
    "steps": [
        sql_step("在 UTF8 集群中新建 GB18030 数据库", "postgres",
                 "CREATE DATABASE %s TEMPLATE template0 ENCODING 'GB18030' LOCALE 'zh_CN.gb18030'" % DATABASE,
                 "返回 CREATE DATABASE", command_succeeds()),
        sql_step("确认数据库内部编码和 locale", "postgres",
                 "SELECT pg_encoding_to_char(encoding), datcollate, datctype FROM pg_database WHERE datname = '%s'" % DATABASE,
                 "返回 GB18030、zh_CN.gb18030、zh_CN.gb18030",
                 rows_equal([["GB18030", "zh_CN.gb18030", "zh_CN.gb18030"]])),
        gb_sql("UTF8 客户端创建日文标识符表", 
               "CREATE TABLE %s (用語 text, 分類コード varchar, 備考1Aだよ char(16))" % TABLE,
               "返回 CREATE TABLE", command_succeeds()),
        gb_sql("创建用语 B-tree 索引", "CREATE INDEX 計算機用語index1 ON %s USING btree (用語)" % TABLE,
               "返回 CREATE INDEX", command_succeeds()),
        gb_sql("创建分类代码 Hash 索引", "CREATE INDEX 計算機用語index2 ON %s USING hash (分類コード)" % TABLE,
               "返回 CREATE INDEX", command_succeeds()),
        gb_sql("插入第一条 GB18030 日文数据", "INSERT INTO %s VALUES ('コンピュータディスプレイ','機A01上')" % TABLE,
               "返回 INSERT 0 1", command_succeeds()),
        gb_sql("插入第二条 GB18030 日文数据", "INSERT INTO %s VALUES ('コンピュータグラフィックス','分B10中')" % TABLE,
               "返回 INSERT 0 1", command_succeeds()),
        gb_sql("插入第三条 GB18030 日文数据", "INSERT INTO %s VALUES ('コンピュータプログラマー','人Z01下')" % TABLE,
               "返回 INSERT 0 1", command_succeeds()),
        gb_sql("对 GB18030 表执行 VACUUM", "VACUUM %s" % TABLE,
               "返回 VACUUM", command_succeeds()),
        gb_sql("查询三条插入的日文数据", "SELECT 用語, 分類コード FROM %s ORDER BY ctid" % TABLE,
               "返回三条文档定义的数据", rows_equal([
                   ["コンピュータディスプレイ", "機A01上"],
                   ["コンピュータグラフィックス", "分B10中"],
                   ["コンピュータプログラマー", "人Z01下"],
               ])),
        gb_sql("精确匹配日文分类代码", "SELECT 用語, 分類コード FROM %s WHERE 分類コード = '人Z01下'" % TABLE,
               "返回 コンピュータプログラマー|人Z01下", rows_equal([["コンピュータプログラマー", "人Z01下"]])),
        gb_sql("正则匹配日文分类代码", "SELECT 用語 FROM %s WHERE 分類コード ~* '人z01下'" % TABLE,
               "返回 コンピュータプログラマー", rows_equal([["コンピュータプログラマー"]])),
        gb_sql("LIKE 精确模式匹配日文分类代码", "SELECT 用語 FROM %s WHERE 分類コード LIKE '_Z01_'" % TABLE,
               "返回 コンピュータプログラマー", rows_equal([["コンピュータプログラマー"]])),
        gb_sql("LIKE 通配模式匹配日文分类代码", "SELECT 用語 FROM %s WHERE 分類コード LIKE '_Z%%'" % TABLE,
               "返回 コンピュータプログラマー", rows_equal([["コンピュータプログラマー"]])),
        gb_sql("正则匹配两个日文用语", "SELECT 用語 FROM %s WHERE 用語 ~ 'コンピュータ[デグ]' ORDER BY ctid" % TABLE,
               "返回显示器和图形两条数据", rows_equal([["コンピュータディスプレイ"], ["コンピュータグラフィックス"]])),
        gb_sql("不区分大小写正则匹配两个日文用语", "SELECT 用語 FROM %s WHERE 用語 ~* 'コンピュータ[デグ]' ORDER BY ctid" % TABLE,
               "返回显示器和图形两条数据", rows_equal([["コンピュータディスプレイ"], ["コンピュータグラフィックス"]])),
        gb_sql("确认日文用语字符长度", "SELECT 用語, character_length(用語)::text FROM %s ORDER BY ctid" % TABLE,
               "返回 12、13、12 个字符", rows_equal([
                   ["コンピュータディスプレイ", "12"], ["コンピュータグラフィックス", "13"], ["コンピュータプログラマー", "12"],
               ])),
        gb_sql("确认日文用语 GB18030 字节长度", "SELECT 用語, octet_length(用語)::text FROM %s ORDER BY ctid" % TABLE,
               "返回 24、26、24 字节", rows_equal([
                   ["コンピュータディスプレイ", "24"], ["コンピュータグラフィックス", "26"], ["コンピュータプログラマー", "24"],
               ])),
        gb_sql("定位日文字符 デ", "SELECT 用語, position('デ' in 用語)::text FROM %s ORDER BY ctid" % TABLE,
               "仅显示器数据的位置为 7，其余为 0", rows_equal([
                   ["コンピュータディスプレイ", "7"], ["コンピュータグラフィックス", "0"], ["コンピュータプログラマー", "0"],
               ])),
        gb_sql("按字符位置截取日文字符串", "SELECT 用語, substring(用語 from 10 for 4) FROM %s ORDER BY ctid" % TABLE,
               "返回 プレイ、ィックス、ラマー", rows_equal([
                   ["コンピュータディスプレイ", "プレイ"], ["コンピュータグラフィックス", "ィックス"], ["コンピュータプログラマー", "ラマー"],
               ])),
        gb_sql("删除日文 SQL 测试表", "DROP TABLE %s" % TABLE,
               "返回 DROP TABLE", command_succeeds()),
        gb_sql("安装 pageinspect 扩展", "CREATE EXTENSION pageinspect",
               "返回 CREATE EXTENSION", command_succeeds()),
        gb_sql("创建 GB18030 页存储测试表", "CREATE TABLE tbl1(name varchar)",
               "返回 CREATE TABLE", command_succeeds()),
        gb_sql("插入一、二、四字节和 ASCII 字符", "INSERT INTO tbl1 VALUES ('䶁'),('雜'),('z'),('䴾'),('ǹ')",
               "返回 INSERT 0 5", command_succeeds()),
        gb_sql("确认页存储测试数据", "SELECT name FROM tbl1 ORDER BY ctid",
               "返回 䶁、雜、z、䴾、ǹ", rows_equal([["䶁"], ["雜"], ["z"], ["䴾"], ["ǹ"]])),
        gb_sql("检查 GB18030 页内 varlena 裸数据长度", 
               "SELECT count(*)::text, bool_and(lp_len IN (26, 27, 29))::text, bool_and(t_data IS NOT NULL)::text FROM heap_page_items(get_raw_page('tbl1', 0))",
               "返回 5|true|true，五条记录均为文档所示的变长裸数据长度", rows_equal([["5", "true", "true"]])),
        gb_sql("删除页存储测试表", "DROP TABLE tbl1",
               "返回 DROP TABLE", command_succeeds()),
    ],
    "teardown": "database fixture 删除 GB18030 测试数据库。",
}
