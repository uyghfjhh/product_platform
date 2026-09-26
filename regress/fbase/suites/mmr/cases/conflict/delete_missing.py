from framework.assertions import output_contains_text
from suites.mmr.cases.node_management.create_group import (PSQL, create_node,
                                                            init_instance, psql, shell)


ROOT = "/tmp/fbase_regress_mmr_conflict_delete_missing_{run_id}"
SOURCE, TARGET = ROOT + "/source", ROOT + "/target"
SOURCE_PORT, TARGET_PORT = "15483", "15484"
DSN = "host=127.0.0.1 port=%s user=postgres dbname=postgres" % SOURCE_PORT


def setup(step):
    step["report"] = False
    return step


def sql(title, script, expected, statement, timeout=30, report_node=None):
    step = shell(title, script, expected, timeout=timeout)
    step["display_sql"] = statement
    step["report_node"] = report_node
    return step


def query(title, port, statement, expected, *markers, **options):
    step = shell(title, "%s -X -P pager=off -h 127.0.0.1 -p %s -U postgres -d postgres -c %r" %
                 (PSQL, port, statement), expected, output_contains_text(*(markers or (expected,))))
    step["display_sql"] = statement
    step["report_node"] = options.get("report_node")
    return step


CASE = {
    "id": "mmr.conflict.delete_missing", "name": "delete_missing 默认 skip 冲突处理",
    "document": "多活功能测试文档.md", "section": "4.1.2", "group": "conflict",
    "fixtures": ["cluster", {"type": "isolated_mmr_node_creation", "data_dir": ROOT}],
    "requirements": {"clusters": ["mmr"], "plugins": ["fdd_mmr"],
                     "writable_node": True, "node": "mmr:mmr1"},
    "evidence_nodes": ["mmr:mmr1"],
    "prerequisites": ["独立重建 4.1.1 的 schema-only 前置：node135 只通过 update_missing 获得 id=1，仍缺少 id=2。"],
    "steps": [
        setup(init_instance("初始化源节点 node134", SOURCE, SOURCE_PORT)), setup(create_node(SOURCE_PORT, "node134")),
        setup(shell("创建 test 表和十行初始数据", psql(SOURCE_PORT, "CREATE TABLE test(id int PRIMARY KEY,title text); INSERT INTO test SELECT generate_series(1,10),'a'"), "返回 CREATE TABLE 和 INSERT 0 10")),
        setup(shell("创建 g1 集群", psql(SOURCE_PORT, "SELECT fdd.create_group('g1')"), "返回 node group create successful")),
        setup(init_instance("初始化 schema-only 节点 node135", TARGET, TARGET_PORT)), setup(create_node(TARGET_PORT, "node135")),
        setup(shell("以 schema-only 加入 g1", psql(TARGET_PORT, "SELECT fdd.join_group('g1','%s',true,'schema-only','table_exist_error')" % DSN), "返回 node join to group complete finished", timeout=90)),
        setup(shell("先更新源端 id=1 以构造文档复用状态", psql(SOURCE_PORT, "UPDATE test SET title='bb' WHERE id=1"), "返回 UPDATE 1")),
        setup(shell("等待 node135 收到 id=1 的 update_missing 处理结果", "for i in $(seq 1 30); do test \"$(%s -X -At -h 127.0.0.1 -p %s -U postgres -d postgres -c %r)\" = bb && exit 0; sleep 1; done; exit 1" % (PSQL, TARGET_PORT, "SELECT title FROM test WHERE id=1"), "30 秒内输出 bb", timeout=35)),
        setup(query("确认 node135 仅有文档前置生成的 id=1", TARGET_PORT, "SELECT count(*) AS row_count FROM test", "返回 1", "1")),
        query("读取发布端实际 debug_logical_replication_streaming", SOURCE_PORT,
              "SHOW debug_logical_replication_streaming", "返回 buffered", "buffered"),
        query("读取订阅端实际 debug_logical_replication_streaming", TARGET_PORT,
              "SHOW debug_logical_replication_streaming", "返回 buffered", "buffered"),
        query("读取发布端实际 logical_decoding_work_mem", SOURCE_PORT,
              "SHOW logical_decoding_work_mem", "返回 64MB", "64MB"),
        query("读取订阅端实际 logical_decoding_work_mem", TARGET_PORT,
              "SHOW logical_decoding_work_mem", "返回 64MB", "64MB"),
        query("读取订阅端 node135 的多活 streaming 模式", TARGET_PORT,
              "SELECT CASE streaming WHEN 'f' THEN 'off' WHEN 't' THEN 'on' WHEN 'p' THEN 'parallel' END AS streaming_mode FROM fdd.mmr_node WHERE node_name='node135'", "返回 off", "off"),
        query("展示冲突触发前 node134 的 test 初始数据", SOURCE_PORT,
              "SELECT id,title FROM test ORDER BY id", "返回十行初始数据，id=1 为 bb、id=2 至 10 为 a", "1", "bb", "10", "a"),
        query("展示冲突触发前 node135 的 test 初始数据", TARGET_PORT,
              "SELECT id,title FROM test ORDER BY id", "仅返回 update_missing 已写入的 id=1,title=bb", "1", "bb"),
        query("确认触发前 node135 没有旧的 delete_missing 冲突记录", TARGET_PORT,
              "SELECT count(*) AS delete_missing_count FROM fdd.mmr_conflict_history WHERE conflict_type='delete_missing'", "返回 0", "0"),
        sql("按文档在 node134 删除 node135 缺失的 id=2", psql(SOURCE_PORT, "DELETE FROM test WHERE id=2"), "返回 DELETE 1", "DELETE FROM test WHERE id=2"),
        sql("等待 node135 记录 delete_missing 冲突", "for i in $(seq 1 30); do v=$(%s -X -At -h 127.0.0.1 -p %s -U postgres -d postgres -c %r); test \"$v\" = 1 && { echo \"$v\"; exit 0; }; sleep 1; done; exit 1" % (PSQL, TARGET_PORT, "SELECT count(*) FROM fdd.mmr_conflict_history WHERE conflict_type='delete_missing'"), "30 秒内存在一条 delete_missing", "SELECT count(*) FROM fdd.mmr_conflict_history WHERE conflict_type='delete_missing'", 35),
        query("确认默认 skip 未改变 node135 的现有数据", TARGET_PORT, "SELECT id,title FROM test ORDER BY id", "仅返回 id=1,title=bb", "1", "bb"),
        query("确认冲突历史记录 delete_missing、skip 和 none 应用结果", TARGET_PORT, "SELECT conflict_type,conflict_resolution,relname,apply_tuple FROM fdd.mmr_conflict_history WHERE conflict_type='delete_missing' ORDER BY local_lsn DESC LIMIT 1", "返回 delete_missing、skip、test 和 apply_tuple=none", "delete_missing", "skip", "test", "none"),
    ],
    "teardown": "以 immediate 停止临时 node134/node135 PostgreSQL 实例；递归删除临时数据目录，连同 test 表、MMR 元数据、订阅、复制槽和本轮冲突记录一并删除。",
}
