from suites.mmr.cases.global_sequence.join_behavior import (PARTED, PARTED_PORT,
                                                             ROOT, SEED, SEED_DSN,
                                                             SEED_PORT, create_node,
                                                             init_instance, psql,
                                                             shell, value)


ISOLATED_ROOT = ROOT + "_nonforce"
SEED_DATA, PARTED_DATA = ISOLATED_ROOT + "/seed", ISOLATED_ROOT + "/parted"
SEQUENCE = "fbase_r_mmr_gseq_nonforce_{run_id}"
REF = "public.%s" % SEQUENCE


def setup(step):
    step["report"] = False
    return step


CASE = {
    "id": "mmr.global_sequence.nonforce_part_metadata",
    "name": "非强制分离回写全局序列最大值",
    "document": "多活功能测试文档.md", "section": "9.6.3.3",
    "group": "global_sequence",
    "fixtures": ["cluster", {"type": "isolated_mmr_node_creation", "data_dir": ISOLATED_ROOT}],
    "requirements": {"clusters": ["mmr"], "plugins": ["fdd_mmr"],
                     "writable_node": True, "node": "mmr:mmr1"},
    "evidence_nodes": ["mmr:mmr1"],
    "prerequisites": [
        "两个临时实例组成 g1；二者均创建同名序列并转换为两节点全局序列。",
        "非强制分离会等待 node102 将当前序列值回传；环境使用 60 秒追增超时并在结束时完整删除。",
    ],
    "steps": [
        init_instance("初始化存活节点 node101", SEED_DATA, SEED_PORT),
        setup(shell("将 node101 分离追增超时设为 60 秒",
              psql(SEED_PORT, "ALTER SYSTEM SET fdd.part_catchup_timeout = '60s'") + "; " +
              psql(SEED_PORT, "SELECT pg_reload_conf()"), "ALTER SYSTEM 和 reload 成功")),
        create_node(SEED_PORT, "node101"),
        shell("建组前创建默认复制集测试表", psql(SEED_PORT, "CREATE TABLE nonforce_probe(id int PRIMARY KEY)"),
              "返回 CREATE TABLE"),
        shell("创建 g1 集群", psql(SEED_PORT, "SELECT fdd.create_group('g1')"),
              "返回 node group create successful"),
        init_instance("初始化待非强制分离节点 node102", PARTED_DATA, PARTED_PORT),
        create_node(PARTED_PORT, "node102"),
        shell("以 all 模式将 node102 加入 g1",
              psql(PARTED_PORT, "SELECT fdd.join_group('g1','%s',true,'all','table_exist_error')" % SEED_DSN),
              "返回 node join to group complete finished", timeout=90),
        setup(shell("在两个成员创建同名普通序列",
              psql(SEED_PORT, "CREATE SEQUENCE %s" % REF) + "; " + psql(PARTED_PORT, "CREATE SEQUENCE %s" % REF),
              "两个成员均返回 CREATE SEQUENCE")),
        shell("转换为两节点全局序列", psql(SEED_PORT, "SELECT fdd.add_global_seq('%s'::regclass,2,true)" % REF),
              "返回 true"),
        value("在 node102 连续取值五次使其 last_value 增长", PARTED_PORT,
              "SELECT max(nextval('%s'::regclass))::text FROM generate_series(1,5)" % REF,
              "10"),
        shell("按文档非强制分离 node102", psql(SEED_PORT, "SELECT fdd.part_node('node102',true,false)"),
              "返回 part node node102 successful", timeout=90),
        value("确认 node102 成为 PARTED", SEED_PORT,
              "SELECT node_state::text FROM fdd.mmr_node WHERE node_name='node102'", "PARTED"),
        value("确认存活节点元数据把 node102 的实际 last_value 回写到 node_maximum", SEED_PORT,
              "SELECT (array_to_string(node_maximum,',') LIKE '%%' || (SELECT node_id::text FROM fdd.mmr_node WHERE node_name='node102') || ':10%%')::text FROM fdd.mmr_global_sequence WHERE seq_name='%s'::regclass" % REF,
              "true"),
    ],
    "teardown": "fixture 以 immediate 停止两个隔离实例并删除整个临时目录；不直接修改产品元数据。",
}
