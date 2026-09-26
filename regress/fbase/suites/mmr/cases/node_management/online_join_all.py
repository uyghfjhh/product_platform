from suites.mmr.cases.conflict.delete_missing import query, setup, sql
from suites.mmr.cases.node_management.create_group import (PSQL, create_node,
                                                            init_instance, psql, shell)


ROOT = "/tmp/fbase_regress_mmr_online_join_all_{run_id}"
SOURCE, JOINER = ROOT + "/source", ROOT + "/joiner"
SOURCE_PORT, JOINER_PORT = "15524", "15525"
DSN = "host=127.0.0.1 port=%s user=postgres dbname=postgres" % SOURCE_PORT
DOC_ROOT = "/home/postgres/fly_dev/postgresql_for_fbase_dev/contrib/fdd_mmr_dev/fdd_mmr/doc/转测文档/必测用例/在线join测试用例"
CREATE, INSERT = DOC_ROOT + "/create_table.sql", DOC_ROOT + "/insert_data.sql"
LONG = "test30_repeat_repeat_repeat_repeat_repeat_repeat_repeat_repeat"


def document_script(port, path, scaled=False):
    command = "%s -X -v ON_ERROR_STOP=1 -h 127.0.0.1 -p %s -U postgres -d postgres" % (PSQL, port)
    if not scaled:
        return "%s -f %s" % (command, path)
    return ("sed -e 's/^\\\\set num_inserts 80000/\\\\set num_inserts 3/' "
            "-e 's/^\\\\set num_inserts1 2000000/\\\\set num_inserts1 3/' "
            "-e 's/^\\\\set num_inserts2 120000/\\\\set num_inserts2 3/' %s | %s" %
            (path, command))


def online_join():
    workload = ("for i in $(seq 1 20); do %s -X -v ON_ERROR_STOP=1 -h 127.0.0.1 -p %s -U postgres -d postgres -c \"INSERT INTO test1(data,data1) VALUES('online_$i',repeat('x',9001)); UPDATE test4 SET data='online_$i' WHERE id=1; UPDATE test24 SET data='online_$i' WHERE id=1; INSERT INTO %s(data,home4) VALUES('online_$i','online');\"; sleep 0.1; done" % (PSQL, SOURCE_PORT, LONG))
    display = ("-- node134：join 期间持续执行 20 次在线业务\n"
               "INSERT INTO test1(data,data1) VALUES('online_<n>',repeat('x',9001));\n"
               "UPDATE test4 SET data='online_<n>' WHERE id=1;\n"
               "UPDATE test24 SET data='online_<n>' WHERE id=1;\n"
               "INSERT INTO %s(data,home4) VALUES('online_<n>','online');\n"
               "-- node135\nSELECT fdd.join_group('g1','%s',true,'all','table_exist_error');" % (LONG, DSN))
    return sql("按文档在在线业务期间以 all 模式加入 g1", "(" + workload + ") & workload_pid=$!; " + psql(JOINER_PORT, "SELECT fdd.join_group('g1','%s',true,'all','table_exist_error')" % DSN) + "; wait $workload_pid", "在线写入期间 join 成功且 20 次业务完成", display, 90)


def consistency(label, port):
    return [
        query("展示%s 的 test1 行数和超 8KB 字段" % label, port, "SELECT count(*) AS row_count,count(*) FILTER (WHERE length(data1)>8000) AS over_8k_count FROM test1", "row_count 和 over_8k_count 均为 23", "23"),
        query("展示%s 的 63 字节长表行数和有效列" % label, port, "SELECT count(*) AS row_count,count(*) FILTER (WHERE home4 IS NOT NULL) AS filled_column_count FROM public.%s" % LONG, "row_count 和 filled_column_count 均为 23", "23"),
        query("展示%s 的在线更新最终值" % label, port, "SELECT (SELECT data FROM test4 WHERE id=1) AS test4_data,(SELECT data FROM test24 WHERE id=1) AS test24_data", "两列均为 online_20", "online_20"),
    ]


CASE = {
    "id": "mmr.node_management.online_join_all", "name": "all 模式在线业务 join 数据一致性",
    "document": "多活功能测试文档.md", "section": "2.3.2.1 测试一", "group": "node_management",
    "fixtures": ["cluster", {"type": "isolated_mmr_node_creation", "data_dir": ROOT}],
    "requirements": {"clusters": ["mmr"], "plugins": ["fdd_mmr"], "writable_node": True, "node": "mmr:mmr1"}, "evidence_nodes": ["mmr:mmr1"],
    "prerequisites": ["复用文档在线 join 用例的 31 表结构、63 字节长表/列和业务 SQL；每表存量缩小为 3 行。", "join 期间源端持续执行 20 次 INSERT/UPDATE，包含 test1 超 8KB 字段、test4/test24 更新和长表插入。"],
    "steps": [
        setup(init_instance("初始化在线业务源节点 node134", SOURCE, SOURCE_PORT)), setup(create_node(SOURCE_PORT, "node134")), setup(shell("按文档 create_table.sql 创建 31 张表", document_script(SOURCE_PORT, CREATE), "返回 31 张 CREATE TABLE")), setup(shell("按文档 insert_data.sql 写入缩小存量", document_script(SOURCE_PORT, INSERT, True), "每张表写入 3 行缩小存量")), setup(shell("创建 g1 集群", psql(SOURCE_PORT, "SELECT fdd.create_group('g1')"), "返回 node group create successful")),
        setup(init_instance("初始化 all join 节点 node135", JOINER, JOINER_PORT)), setup(create_node(JOINER_PORT, "node135")),
        query("展示 join 前源端 test1 存量和超 8KB 字段", SOURCE_PORT, "SELECT count(*) AS row_count,count(*) FILTER (WHERE length(data1)>8000) AS over_8k_count FROM test1", "两列均为 3", "3"), online_join(), *consistency("源端 join 完成后", SOURCE_PORT), *consistency("node135 join 完成后", JOINER_PORT),
    ],
    "teardown": "以 immediate 停止 node134/node135 临时实例；递归删除临时目录，连同文档的 31 张表、序列、MMR 元数据、订阅、复制槽和在线业务数据一并删除。",
}
