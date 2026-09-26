from suites.mmr.cases.node_management.create_group import (PSQL, create_node,
                                                            init_instance, psql, shell)
from suites.mmr.cases.conflict.delete_missing import query, setup, sql


ROOT = "/tmp/fbase_regress_mmr_conflict_insert_exists_{run_id}"
SOURCE, TARGET = ROOT + "/source", ROOT + "/target"
SOURCE_PORT, TARGET_PORT = "15485", "15486"
DSN = "host=127.0.0.1 port=%s user=postgres dbname=postgres" % SOURCE_PORT


CASE = {
    "id": "mmr.conflict.insert_exists", "name": "insert_exists 默认 update_if_newer 冲突处理",
    "document": "多活功能测试文档.md", "section": "4.1.3", "group": "conflict",
    "fixtures": ["cluster", {"type": "isolated_mmr_node_creation", "data_dir": ROOT}],
    "requirements": {"clusters": ["mmr"], "plugins": ["fdd_mmr"], "writable_node": True, "node": "mmr:mmr1"},
    "evidence_nodes": ["mmr:mmr1"],
    "prerequisites": ["node134 有 id=3,title=a；node135 以 schema-only 加入，test 表缺少 id=3。"],
    "steps": [
        setup(init_instance("初始化源节点 node134", SOURCE, SOURCE_PORT)), setup(create_node(SOURCE_PORT, "node134")),
        setup(shell("创建 test 表和十行初始数据", psql(SOURCE_PORT, "CREATE TABLE test(id int PRIMARY KEY,title text); INSERT INTO test SELECT generate_series(1,10),'a'"), "返回 CREATE TABLE 和 INSERT 0 10")),
        setup(shell("创建 g1 集群", psql(SOURCE_PORT, "SELECT fdd.create_group('g1')"), "返回 node group create successful")),
        setup(init_instance("初始化 schema-only 节点 node135", TARGET, TARGET_PORT)), setup(create_node(TARGET_PORT, "node135")),
        setup(shell("以 schema-only 加入 g1", psql(TARGET_PORT, "SELECT fdd.join_group('g1','%s',true,'schema-only','table_exist_error')" % DSN), "返回 node join to group complete finished", timeout=90)),
        query("读取发布端实际 debug_logical_replication_streaming", SOURCE_PORT, "SHOW debug_logical_replication_streaming", "返回 buffered", "buffered"),
        query("读取订阅端实际 debug_logical_replication_streaming", TARGET_PORT, "SHOW debug_logical_replication_streaming", "返回 buffered", "buffered"),
        query("读取发布端实际 logical_decoding_work_mem", SOURCE_PORT, "SHOW logical_decoding_work_mem", "返回 64MB", "64MB"),
        query("读取订阅端实际 logical_decoding_work_mem", TARGET_PORT, "SHOW logical_decoding_work_mem", "返回 64MB", "64MB"),
        query("展示冲突触发前 node134 的 test 初始数据", SOURCE_PORT, "SELECT id,title FROM test ORDER BY id", "返回十行初始数据", "3", "a"),
        query("展示冲突触发前 node135 的 test 初始数据", TARGET_PORT, "SELECT id,title FROM test ORDER BY id", "返回 0 行", "(0 rows)"),
        query("确认触发前 node134 没有旧的 insert_exists 冲突记录", SOURCE_PORT, "SELECT count(*) AS insert_exists_count FROM fdd.mmr_conflict_history WHERE conflict_type='insert_exists'", "返回 0", "0"),
        sql("按文档在 node135 插入冲突的 id=3 新数据", psql(TARGET_PORT, "INSERT INTO test VALUES(3,'aa')"), "返回 INSERT 0 1", "INSERT INTO test VALUES(3,'aa')"),
        sql("等待 node134 按 update_if_newer 用远端数据更新 id=3", "for i in $(seq 1 30); do v=$(%s -X -At -h 127.0.0.1 -p %s -U postgres -d postgres -c %r); test \"$v\" = aa && { echo \"$v\"; exit 0; }; sleep 1; done; exit 1" % (PSQL, SOURCE_PORT, "SELECT title FROM test WHERE id=3"), "30 秒内输出 aa", "SELECT title FROM test WHERE id=3", 35),
        query("确认 update_if_newer 已用远端数据更新 node134", SOURCE_PORT, "SELECT id,title FROM test WHERE id=3", "返回 id=3,title=aa", "3", "aa"),
        query("确认 node134 冲突历史记录默认 update_if_newer 和远端应用", SOURCE_PORT, "SELECT conflict_type,conflict_resolution,relname,apply_tuple FROM fdd.mmr_conflict_history WHERE conflict_type='insert_exists' ORDER BY local_lsn DESC LIMIT 1", "返回 insert_exists、update_if_newer、test 和 remote", "insert_exists", "update_if_newer", "test", "remote"),
    ],
    "teardown": "以 immediate 停止临时 node134/node135 PostgreSQL 实例；递归删除临时数据目录，连同 test 表、MMR 元数据、订阅、复制槽和本轮冲突记录一并删除。",
}
