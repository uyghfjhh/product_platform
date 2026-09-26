from framework.assertions import command_fails, command_succeeds, output_contains
from framework.steps import command_step
from suites.mmr.cases.node_management.create_group import (PSQL, create_node,
                                                            init_instance, psql)


ROOT = "/tmp/fbase_regress_mmr_gseq_join_{run_id}"
SEED, PARTED, JOINER = ROOT + "/seed", ROOT + "/parted", ROOT + "/joiner"
# Keep isolated servers below the host's 9000-65500 ephemeral-client range.
SEED_PORT, PARTED_PORT, JOINER_PORT = "7541", "7542", "7543"
SEED_DSN = "host=127.0.0.1 port=%s user=postgres dbname=postgres" % SEED_PORT
SEQUENCE = "fbase_r_mmr_gseq_join_{run_id}"
REF = "public.%s" % SEQUENCE


def shell(title, script, expected, assertion=command_succeeds(), timeout=60,
          continue_on_failure=False):
    return command_step(title, ["sh", "-ec", script], expected, assertion,
                        node="mmr:mmr1", timeout=timeout,
                        continue_on_failure=continue_on_failure)


def value(title, port, sql, expected, assertion=None, timeout=30,
          continue_on_failure=False):
    assertion = assertion or output_contains(expected)
    return shell(title,
                 "%s -X -At -F '|' -h 127.0.0.1 -p %s -U postgres -d postgres -c %r" %
                 (PSQL, port, sql), "输出 %s" % expected, assertion, timeout,
                 continue_on_failure)


def setup(step):
    step["report"] = False
    return step


CASE = {
    "id": "mmr.global_sequence.join_behavior",
    "name": "全局序列强制分离节点与新节点加入",
    "document": "多活功能测试文档.md", "section": "9.6.3.2",
    "group": "global_sequence",
    "fixtures": ["cluster", {"type": "isolated_mmr_node_creation", "data_dir": ROOT}],
    "requirements": {"clusters": ["mmr"], "plugins": ["fdd_mmr"],
                     "writable_node": True, "node": "mmr:mmr1"},
    "evidence_nodes": ["mmr:mmr1"],
    "prerequisites": [
        "三个临时实例使用 loopback DSN；seed 和 parted 先组成两节点集群。",
        "测试序列和所有多活元数据均在临时实例中创建，结束后 fixture 删除整个临时目录。",
    ],
    "steps": [
        init_instance("初始化全局序列建组节点 node101", SEED, SEED_PORT),
        setup(shell("将 node101 的分离追增超时设为 60 秒",
              psql(SEED_PORT, "ALTER SYSTEM SET fdd.part_catchup_timeout = '60s'") + "; " +
              psql(SEED_PORT, "SELECT pg_reload_conf()"),
              "ALTER SYSTEM 和 reload 成功")),
        create_node(SEED_PORT, "node101"),
        shell("建组前创建默认复制集的测试表", psql(SEED_PORT, "CREATE TABLE gseq_join_probe(id int PRIMARY KEY)"),
              "返回 CREATE TABLE"),
        shell("在 node101 创建 g1 集群", psql(SEED_PORT, "SELECT fdd.create_group('g1')"),
              "返回 node group create successful"),
        init_instance("初始化待强制分离节点 node102", PARTED, PARTED_PORT),
        create_node(PARTED_PORT, "node102"),
        shell("以 all 模式将 node102 加入 g1",
              psql(PARTED_PORT, "SELECT fdd.join_group('g1','%s',true,'all','table_exist_error')" % SEED_DSN),
              "返回 node join to group complete finished", timeout=90),
        setup(shell("在两个成员创建同名普通序列",
              psql(SEED_PORT, "CREATE SEQUENCE %s" % REF) + "; " + psql(PARTED_PORT, "CREATE SEQUENCE %s" % REF),
              "两个成员均返回 CREATE SEQUENCE")),
        shell("按文档将序列转换为两节点全局序列",
              psql(SEED_PORT, "SELECT fdd.add_global_seq('%s'::regclass,2,true)" % REF),
              "返回 true"),
        value("确认初始全局序列没有强制分离节点", SEED_PORT,
              "SELECT node_count::text,force_nodeid::text,seq_state::text FROM fdd.mmr_global_sequence WHERE seq_name='%s'::regclass" % REF,
              "2||d"),
        shell("按文档强制分离 node102", psql(SEED_PORT, "SELECT fdd.part_node('node102',true,true)"),
              "返回 part node node102 successful", timeout=90),
        shell("按文档删除已强制分离的 node102", psql(SEED_PORT, "SELECT fdd.drop_node('node102',true)"),
              "返回 drop node node102 successful", timeout=60),
        value("确认全局序列记录 node102 的强制分离 node_id", SEED_PORT,
              "SELECT (force_nodeid IS NOT NULL AND array_length(force_nodeid,1)=1)::text FROM fdd.mmr_global_sequence WHERE seq_name='%s'::regclass" % REF,
              "true"),
        init_instance("初始化首次加入将被拒绝的 node103", JOINER, JOINER_PORT),
        shell("在 node103 创建文档要求的同名普通序列", psql(JOINER_PORT, "CREATE SEQUENCE %s" % REF),
              "返回 CREATE SEQUENCE"),
        create_node(JOINER_PORT, "node103"),
        shell("按文档在 force_nodeid 非空时加入新节点并确认失败",
              psql(JOINER_PORT, "SELECT fdd.join_group('g1','%s',true,'all','table_exist_error')" % SEED_DSN),
              "返回 global sequence force_nodeid 错误", command_fails("force_nodeid isn't null"),
              timeout=90, continue_on_failure=True),
        value("确认失败的加入节点在 seed 元数据中为 JOIN_START", SEED_PORT,
              "SELECT node_state::text FROM fdd.mmr_node WHERE node_name='node103'", "JOIN_START"),
        shell("按文档先分离失败中的 node103", psql(SEED_PORT, "SELECT fdd.part_node('node103',true,false)"),
              "返回 part node node103 successful", timeout=90),
        shell("按文档删除失败中的 node103", psql(SEED_PORT, "SELECT fdd.drop_node('node103',true)"),
              "返回 drop node node103 successful", timeout=60),
        create_node(JOINER_PORT, "node103"),
        shell("按文档为强制分离 node102 设置序列值以清理 force_nodeid",
              psql(SEED_PORT, "SELECT fdd.set_global_seq(ARRAY['%s'::regclass],NULL,2,100026)" % REF),
              "返回 true"),
        value("确认 force_nodeid 已清空且 node102 的最大值已更新", SEED_PORT,
              "SELECT (force_nodeid='{}')::text,(node_maximum::text LIKE '%%2:100026%%')::text FROM fdd.mmr_global_sequence WHERE seq_name='%s'::regclass" % REF,
              "true|true"),
        shell("按文档重新以 all 模式将 node103 加入 g1",
              psql(JOINER_PORT, "SELECT fdd.join_group('g1','%s',true,'all','table_exist_error')" % SEED_DSN),
              "返回 node join to group complete finished", timeout=120),
        value("确认新节点加入后序列元数据增加其 node_id 且待刷新", SEED_PORT,
              "SELECT node_count::text,(node_maximum::text LIKE '%%' || (SELECT node_id::text FROM fdd.mmr_node WHERE node_name='node103') || ':0%%')::text,seq_state::text FROM fdd.mmr_global_sequence WHERE seq_name='%s'::regclass" % REF,
              "3|true|i"),
        value("确认重新加入的 node103 为 ACTIVE", SEED_PORT,
              "SELECT node_state::text FROM fdd.mmr_node WHERE node_name='node103'", "ACTIVE"),
    ],
    "teardown": "fixture 以 immediate 停止三个隔离实例并删除整个临时目录；不直接修改产品元数据。",
}
