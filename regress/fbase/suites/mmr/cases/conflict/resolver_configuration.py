from suites.mmr.cases.node_management.create_group import (PSQL, create_node,
                                                            init_instance, psql, shell)
from suites.mmr.cases.conflict.delete_missing import query, setup, sql


ROOT = "/tmp/fbase_regress_mmr_conflict_resolver_{run_id}"
SOURCE, TARGET = ROOT + "/source", ROOT + "/target"
SOURCE_PORT, TARGET_PORT = "15503", "15504"
DSN = "host=127.0.0.1 port=%s user=postgres dbname=postgres" % SOURCE_PORT
TABLE = "resolver_{run_id}"


CASE = {
    "id": "mmr.conflict.resolver_configuration", "name": "冲突处理策略本地、指定节点和全节点设置",
    "document": "多活功能测试文档.md", "section": "4.2 本地配置,4.2 指定节点配置,4.2 全节点配置", "group": "conflict",
    "fixtures": ["cluster", {"type": "isolated_mmr_node_creation", "data_dir": ROOT}],
    "requirements": {"clusters": ["mmr"], "plugins": ["fdd_mmr"], "writable_node": True, "node": "mmr:mmr1"},
    "evidence_nodes": ["mmr:mmr1"],
    "prerequisites": ["node134 有 id=4,title=a，node135 schema-only 加入且缺少 id=4；全部策略改动仅发生于临时实例。"],
    "steps": [
        setup(init_instance("初始化 node134", SOURCE, SOURCE_PORT)), setup(create_node(SOURCE_PORT, "node134")),
        setup(shell("创建测试表和文档冲突行", psql(SOURCE_PORT, "CREATE TABLE public.%s(id int PRIMARY KEY,title text); INSERT INTO public.%s VALUES(4,'a')" % (TABLE,TABLE)), "返回 CREATE TABLE 和 INSERT 0 1")),
        setup(shell("创建 g1 集群", psql(SOURCE_PORT, "SELECT fdd.create_group('g1')"), "返回 node group create successful")),
        setup(init_instance("初始化 node135", TARGET, TARGET_PORT)), setup(create_node(TARGET_PORT, "node135")),
        setup(shell("以 schema-only 加入 g1", psql(TARGET_PORT, "SELECT fdd.join_group('g1','%s',true,'schema-only','table_exist_error')" % DSN), "返回 node join to group complete finished", timeout=90)),
        query("展示 node134 默认 insert_exists 策略", SOURCE_PORT, "SELECT conflict_type,conflict_action FROM fdd.mmr_conflict_resolvers WHERE conflict_type='insert_exists'", "返回 update_if_newer", "insert_exists", "update_if_newer"),
        query("展示 node135 默认 update_origin_change 策略", TARGET_PORT, "SELECT conflict_type,conflict_action FROM fdd.mmr_conflict_resolvers WHERE conflict_type='update_origin_change'", "返回 update_if_newer", "update_origin_change", "update_if_newer"),
        query("展示两端默认 update_missing 策略", SOURCE_PORT, "SELECT node_name,result FROM fdd.run_on_all_nodes('SELECT conflict_action FROM fdd.mmr_conflict_resolvers WHERE conflict_type=''update_missing''') ORDER BY node_name", "两节点均为 insert_or_skip", "insert_or_skip"),
        query("确认触发前没有旧的 insert_exists 记录", SOURCE_PORT, "SELECT count(*) AS insert_exists_count FROM fdd.mmr_conflict_history WHERE conflict_type='insert_exists'", "返回 0", "0"),
        sql("按文档将 node134 本地 insert_exists 设置为 error", psql(SOURCE_PORT, "SELECT * FROM fdd.alter_local_node_set_conflict_resolver('insert_exists','error')"), "返回 node134|insert_exists|error", "SELECT * FROM fdd.alter_local_node_set_conflict_resolver('insert_exists','error')"),
        query("确认 node134 修改后的策略和配置视图", SOURCE_PORT, "SELECT r.conflict_type,r.conflict_action,(SELECT conflict_action FROM fdd.mmr_conflict_res_config WHERE conflict_type='insert_exists') AS configured_action FROM fdd.mmr_conflict_resolvers r WHERE r.conflict_type='insert_exists'", "返回 error", "insert_exists", "error"),
        sql("在 node135 插入与 node134 主键冲突的 id=4", psql(TARGET_PORT, "INSERT INTO public.%s VALUES(4,'aa')" % TABLE), "返回 INSERT 0 1", "INSERT INTO public.%s VALUES(4,'aa')" % TABLE),
        sql("等待 node134 记录 error 策略下的 insert_exists", "for i in $(seq 1 30); do v=$(%s -X -At -h 127.0.0.1 -p %s -U postgres -d postgres -c %r); test \"$v\" = 1 && { echo \"$v\"; exit 0; }; sleep 1; done; exit 1" % (PSQL,SOURCE_PORT,"SELECT count(*) FROM fdd.mmr_conflict_history WHERE conflict_type='insert_exists' AND conflict_resolution='error'"), "30 秒内存在 error 处理记录", "SELECT count(*) FROM fdd.mmr_conflict_history WHERE conflict_type='insert_exists' AND conflict_resolution='error'",35),
        query("展示本地 error 策略的冲突记录", SOURCE_PORT, "SELECT conflict_type,conflict_resolution,relname,apply_tuple FROM fdd.mmr_conflict_history WHERE conflict_type='insert_exists' ORDER BY local_lsn DESC LIMIT 1", "返回 insert_exists、error 和 none", "insert_exists", "error", TABLE, "none"),
        sql("按文档从 node134 指定 node135 的 update_origin_change 为 error", psql(SOURCE_PORT, "SELECT * FROM fdd.alter_node_set_conflict_resolver('node135','update_origin_change','error')"), "返回 node135|update_origin_change|error", "SELECT * FROM fdd.alter_node_set_conflict_resolver('node135','update_origin_change','error')"),
        query("确认 node135 指定策略及配置视图", TARGET_PORT, "SELECT r.conflict_type,r.conflict_action,(SELECT conflict_action FROM fdd.mmr_conflict_res_config WHERE conflict_type='update_origin_change') AS configured_action FROM fdd.mmr_conflict_resolvers r WHERE r.conflict_type='update_origin_change'", "返回 error", "update_origin_change", "error"),
        sql("按文档将所有节点 update_missing 设置为 skip", psql(SOURCE_PORT, "SELECT * FROM fdd.alter_node_set_conflict_resolver(NULL,'update_missing','skip')"), "返回两个节点的 update_missing|skip", "SELECT * FROM fdd.alter_node_set_conflict_resolver(NULL,'update_missing','skip')"),
        query("确认 node134 全局策略已变为 skip", SOURCE_PORT, "SELECT conflict_type,conflict_action FROM fdd.mmr_conflict_resolvers WHERE conflict_type='update_missing'", "返回 update_missing|skip", "update_missing", "skip"),
        query("确认 node135 全局策略已变为 skip", TARGET_PORT, "SELECT conflict_type,conflict_action FROM fdd.mmr_conflict_resolvers WHERE conflict_type='update_missing'", "返回 update_missing|skip", "update_missing", "skip"),
    ],
    "teardown": "以 immediate 停止临时 node134/node135 PostgreSQL 实例；递归删除临时数据目录，连同测试表、MMR 元数据、订阅、复制槽、冲突策略和本轮冲突记录一并删除。",
}
