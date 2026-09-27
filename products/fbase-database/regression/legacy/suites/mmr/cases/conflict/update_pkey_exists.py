from suites.mmr.cases.node_management.create_group import (PSQL, create_node,
                                                            init_instance, psql, shell)
from suites.mmr.cases.conflict.delete_missing import query, setup, sql


ROOT = "/tmp/fbase_regress_mmr_conflict_update_pkey_{run_id}"
SOURCE, TARGET = ROOT + "/source", ROOT + "/target"
SOURCE_PORT, TARGET_PORT = "15487", "15488"
DSN = "host=127.0.0.1 port=%s user=postgres dbname=postgres" % SOURCE_PORT


CASE = {
    "id": "mmr.conflict.update_pkey_exists", "name": "update_pkey_exists 默认 update_if_newer 冲突处理",
    "document": "多活功能测试文档.md", "section": "4.1.4", "group": "conflict",
    "fixtures": ["cluster", {"type": "isolated_mmr_node_creation", "data_dir": ROOT}],
    "requirements": {"clusters": ["mmr"], "plugins": ["fdd_mmr"], "writable_node": True, "node": "mmr:mmr1"},
    "evidence_nodes": ["mmr:mmr1"],
    "prerequisites": ["schema-only join 后先以 update_missing 将 id=1 同步为 bb；随后按文档通过 run_on_all_nodes 两端同时更新主键。"],
    "steps": [
        setup(init_instance("初始化源节点 node134", SOURCE, SOURCE_PORT)), setup(create_node(SOURCE_PORT, "node134")),
        setup(shell("创建 test 表和十行初始数据", psql(SOURCE_PORT, "CREATE TABLE test(id int PRIMARY KEY,title text); INSERT INTO test SELECT generate_series(1,10),'a'"), "返回 CREATE TABLE 和 INSERT 0 10")),
        setup(shell("创建 g1 集群", psql(SOURCE_PORT, "SELECT fdd.create_group('g1')"), "返回 node group create successful")),
        setup(init_instance("初始化 schema-only 节点 node135", TARGET, TARGET_PORT)), setup(create_node(TARGET_PORT, "node135")),
        setup(shell("以 schema-only 加入 g1", psql(TARGET_PORT, "SELECT fdd.join_group('g1','%s',true,'schema-only','table_exist_error')" % DSN), "返回 node join to group complete finished", timeout=90)),
        setup(shell("构造 id=1 的 update_missing 前置", psql(SOURCE_PORT, "UPDATE test SET title='bb' WHERE id=1"), "返回 UPDATE 1")),
        setup(shell("等待 node135 收到 id=1", "for i in $(seq 1 30); do test \"$(%s -X -At -h 127.0.0.1 -p %s -U postgres -d postgres -c %r)\" = bb && exit 0; sleep 1; done; exit 1" % (PSQL, TARGET_PORT, "SELECT title FROM test WHERE id=1"), "30 秒内输出 bb", timeout=35)),
        query("读取发布端实际 debug_logical_replication_streaming", SOURCE_PORT, "SHOW debug_logical_replication_streaming", "返回 buffered", "buffered"),
        query("读取订阅端实际 debug_logical_replication_streaming", TARGET_PORT, "SHOW debug_logical_replication_streaming", "返回 buffered", "buffered"),
        query("读取两端实际 logical_decoding_work_mem", SOURCE_PORT, "SHOW logical_decoding_work_mem", "返回 64MB", "64MB"),
        query("展示触发前 node134 的 id=1", SOURCE_PORT, "SELECT id,title FROM test WHERE id=1", "返回 1|bb", "1", "bb"),
        query("展示触发前 node135 的 id=1", TARGET_PORT, "SELECT id,title FROM test WHERE id=1", "返回 1|bb", "1", "bb"),
        query("确认触发前没有旧的 update_pkey_exists 记录", SOURCE_PORT, "SELECT count(*) AS update_pkey_exists_count FROM fdd.mmr_conflict_history WHERE conflict_type='update_pkey_exists'", "返回 0", "0"),
        sql("按文档使用 run_on_all_nodes 同时修改两端主键", psql(SOURCE_PORT, "SELECT * FROM fdd.run_on_all_nodes('UPDATE test SET id=1000 WHERE id=1')"), "两个节点均返回 UPDATE 1", "SELECT * FROM fdd.run_on_all_nodes('UPDATE test SET id=1000 WHERE id=1')"),
        sql("等待 update_pkey_exists 冲突记录", "for i in $(seq 1 30); do v=$(%s -X -At -h 127.0.0.1 -p %s -U postgres -d postgres -c %r); test \"$v\" = 1 && { echo \"$v\"; exit 0; }; sleep 1; done; exit 1" % (PSQL, SOURCE_PORT, "SELECT count(*) FROM fdd.mmr_conflict_history WHERE conflict_type='update_pkey_exists'"), "30 秒内存在一条 update_pkey_exists", "SELECT count(*) FROM fdd.mmr_conflict_history WHERE conflict_type='update_pkey_exists'", 35),
        query("确认两端最终只保留 id=1000,title=bb", SOURCE_PORT, "SELECT id,title FROM test WHERE id IN (1,1000) ORDER BY id", "返回 id=1000,title=bb", "1000", "bb"),
        query("确认 update_pkey_exists 的默认处理及按提交时间选择的实际元组", SOURCE_PORT, "SELECT conflict_type,conflict_resolution,relname,apply_tuple,CASE WHEN position('local' in apply_tuple::text)>0 OR position('remote' in apply_tuple::text)>0 THEN 'valid' ELSE 'invalid' END AS apply_tuple_check FROM fdd.mmr_conflict_history WHERE conflict_type='update_pkey_exists' ORDER BY local_lsn DESC LIMIT 1", "返回 update_pkey_exists、update_if_newer、test、实际 local/remote 和 valid", "update_pkey_exists", "update_if_newer", "test", "valid"),
    ],
    "teardown": "以 immediate 停止临时 node134/node135 PostgreSQL 实例；递归删除临时数据目录，连同 test 表、MMR 元数据、订阅、复制槽和本轮冲突记录一并删除。",
}
