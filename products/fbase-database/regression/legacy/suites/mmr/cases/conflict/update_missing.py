from suites.mmr.cases.node_management.create_group import (PSQL, create_node,
                                                            init_instance, psql, shell)
from suites.mmr.cases.conflict.delete_missing import query, setup, sql


ROOT = "/tmp/fbase_regress_mmr_conflict_update_missing_{run_id}"
SOURCE, TARGET = ROOT + "/source", ROOT + "/target"
SOURCE_PORT, TARGET_PORT = "15481", "15482"
DSN = "host=127.0.0.1 port=%s user=postgres dbname=postgres" % SOURCE_PORT


CASE = {
    "id": "mmr.conflict.update_missing", "name": "update_missing 默认 insert_or_skip 冲突处理",
    "document": "多活功能测试文档.md", "section": "4.1.1", "group": "conflict",
    "fixtures": ["cluster", {"type": "isolated_mmr_node_creation", "data_dir": ROOT}],
    "requirements": {"clusters": ["mmr"], "plugins": ["fdd_mmr"],
                     "writable_node": True, "node": "mmr:mmr1"},
    "evidence_nodes": ["mmr:mmr1"],
    "prerequisites": ["两个临时实例启用 fdd_mmr；node135 以 schema-only 加入，刻意不复制初始十行数据。"],
    "steps": [
        setup(init_instance("初始化源节点 node134", SOURCE, SOURCE_PORT)), setup(create_node(SOURCE_PORT, "node134")),
        setup(shell("创建 test 表并写入十行初始数据", psql(SOURCE_PORT, "CREATE TABLE test(id int PRIMARY KEY,title text); INSERT INTO test SELECT generate_series(1,10),'a'"), "返回 CREATE TABLE 和 INSERT 0 10")),
        setup(shell("创建 g1 集群", psql(SOURCE_PORT, "SELECT fdd.create_group('g1')"), "返回 node group create successful")),
        setup(init_instance("初始化 schema-only 加入节点 node135", TARGET, TARGET_PORT)), setup(create_node(TARGET_PORT, "node135")),
        setup(shell("以 schema-only 加入 g1", psql(TARGET_PORT, "SELECT fdd.join_group('g1','%s',true,'schema-only','table_exist_error')" % DSN), "返回 node join to group complete finished", timeout=90)),
        query("展示冲突触发前 node134 的 test 初始数据", SOURCE_PORT, "SELECT id,title FROM test ORDER BY id", "返回十行初始数据", "1", "a", "10"),
        query("展示冲突触发前 node135 的 test 初始数据", TARGET_PORT, "SELECT id,title FROM test ORDER BY id", "返回 0 行", "(0 rows)"),
        query("确认触发前 node135 没有旧的 update_missing 冲突记录", TARGET_PORT, "SELECT count(*) AS update_missing_count FROM fdd.mmr_conflict_history WHERE conflict_type='update_missing'", "返回 0", "0"),
        sql("按文档在 node134 更新缺失行", psql(SOURCE_PORT, "UPDATE test SET title='bb' WHERE id=1"), "返回 UPDATE 1", "UPDATE test SET title='bb' WHERE id=1"),
        sql("等待 node135 将 update_missing 按 insert_or_skip 应用为新行", "for i in $(seq 1 30); do v=$(%s -X -At -h 127.0.0.1 -p %s -U postgres -d postgres -c %r); test \"$v\" = bb && { echo \"$v\"; exit 0; }; sleep 1; done; exit 1" % (PSQL, TARGET_PORT, "SELECT title FROM test WHERE id=1"), "30 秒内输出 bb", "SELECT title FROM test WHERE id=1", 35),
        query("确认 update_missing 默认 insert_or_skip 结果", TARGET_PORT, "SELECT id,title FROM test ORDER BY id", "仅返回 id=1,title=bb", "1", "bb"),
        query("确认冲突历史记录类型、默认处理策略和远端应用结果", TARGET_PORT, "SELECT conflict_type,conflict_resolution,relname,apply_tuple FROM fdd.mmr_conflict_history WHERE conflict_type='update_missing' ORDER BY local_lsn DESC LIMIT 1", "返回 update_missing、insert_or_skip、test 和 remote", "update_missing", "insert_or_skip", "test", "remote"),
    ],
    "teardown": "以 immediate 停止临时 node134/node135 PostgreSQL 实例；递归删除临时数据目录，连同 test 表、MMR 元数据、订阅、复制槽和本轮冲突记录一并删除。",
}
