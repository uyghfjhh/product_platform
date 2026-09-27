"""MMR functional document 9.6.2.3 test 2: set an increment-offset sequence by node id."""

from framework.assertions import output_contains_text
from suites.mmr.cases.node_management.create_group import (PSQL, create_node,
                                                            init_instance, psql,
                                                            shell)


ROOT = "/tmp/fbase_regress_mmr_gseq_node_id_{run_id}"
NODE1, NODE2, NODE3 = ROOT + "/node1", ROOT + "/node2", ROOT + "/node3"
PORT1, PORT2, PORT3 = "15641", "15642", "15643"
DSN1 = "host=127.0.0.1 port=%s user=postgres dbname=postgres" % PORT1
SEQUENCE = "fbase_r_mmr_gseq_node_id_{run_id}"
REF = "public.%s" % SEQUENCE


def setup(step):
    step["report"] = False
    return step


def sql(title, port, statement, expected, *markers, **options):
    step = shell(
        title,
        "%s -X -P pager=off -h 127.0.0.1 -p %s -U postgres -d postgres -c %r" %
        (PSQL, port, statement), expected,
        output_contains_text(*(markers or (expected,))), timeout=options.get("timeout", 30))
    step["display_sql"] = statement
    step["report_node"] = options.get("report_node")
    return step


CASE = {
    "id": "mmr.global_sequence.increment_offset_node_id",
    "name": "步进和偏移全局序列指定节点值设置",
    "document": "多活功能测试文档.md",
    "section": "9.6.2.3 测试二",
    "group": "global_sequence",
    "fixtures": ["cluster", {"type": "isolated_mmr_node_creation", "data_dir": ROOT}],
    "requirements": {"clusters": ["mmr"], "plugins": ["fdd_mmr"],
                     "writable_node": True, "node": "mmr:mmr1"},
    "evidence_nodes": ["mmr:mmr1"],
    "test_topology": {
        "summary": "节点数=3；node1/node2/node3 为对等可写 MMR 主节点；无物理备库、无独立普通逻辑复制。",
        "nodes": [
            {"name": "node1", "role": "MMR primary", "host": "127.0.0.1", "port": PORT1, "data_dir": NODE1},
            {"name": "node2", "role": "MMR primary", "host": "127.0.0.1", "port": PORT2, "data_dir": NODE2},
            {"name": "node3", "role": "MMR primary", "host": "127.0.0.1", "port": PORT3, "data_dir": NODE3},
        ],
        "relations": ["MMR 多活: node1 <-> node2 <-> node3（双向复制）", "物理流复制: 无"],
    },
    "prerequisites": [
        "按文档建立独立三节点 g1；强制分离和删除仅作用于临时 node3。",
        "序列名包含 run_id，初始不存在；临时目录和全部 MMR 元数据在结束后删除。",
    ],
    "steps": [
        setup(init_instance("初始化 node1", NODE1, PORT1)),
        setup(create_node(PORT1, "node1")),
        setup(shell("创建三节点集群的默认复制集测试表",
                    psql(PORT1, "CREATE TABLE public.global_sequence_probe(id int PRIMARY KEY)"),
                    "返回 CREATE TABLE")),
        setup(shell("在 node1 创建 g1", psql(PORT1, "SELECT fdd.create_group('g1')"),
                    "返回 node group create successful")),
        setup(shell("将 node1 的分离追增超时设为 60 秒",
                    psql(PORT1, "ALTER SYSTEM SET fdd.part_catchup_timeout = '60s'") + "; " +
                    psql(PORT1, "SELECT pg_reload_conf()"),
                    "ALTER SYSTEM 和 reload 成功")),
        setup(init_instance("初始化 node2", NODE2, PORT2)),
        setup(create_node(PORT2, "node2")),
        setup(shell("以 all 模式将 node2 加入 g1",
                    psql(PORT2, "SELECT fdd.join_group('g1','%s',true,'all','table_exist_error')" % DSN1),
                    "返回 node join to group complete finished", timeout=90)),
        setup(init_instance("初始化 node3", NODE3, PORT3)),
        setup(create_node(PORT3, "node3")),
        setup(shell("以 all 模式将 node3 加入 g1",
                    psql(PORT3, "SELECT fdd.join_group('g1','%s',true,'all','table_exist_error')" % DSN1),
                    "返回 node join to group complete finished", timeout=120)),
        sql("在三个成员创建同名普通序列", PORT1,
            "SELECT count(*)::text || '|' || bool_and(success)::text FROM fdd.run_on_all_nodes('CREATE SEQUENCE %s START WITH 1 INCREMENT BY 1')" % REF,
            "返回 3|true", "3|true", report_node="node1"),
        sql("按文档转换为 node_count=3 的全局序列", PORT1,
            "SELECT fdd.add_global_seq('%s'::regclass,3,true)::text" % REF,
            "返回 true", "true", report_node="node1"),
        sql("读取 node3 的实际 node_id", PORT1,
            "SELECT node_id::text FROM fdd.mmr_node WHERE node_name='node3'",
            "返回 3", "3", report_node="node1"),
        sql("按文档更新非强制分离 node3 的指定序列值", PORT1,
            "SELECT fdd.set_global_seq(ARRAY['%s'::regclass],NULL,(SELECT node_id FROM fdd.mmr_node WHERE node_name='node3'),32678)::text" % REF,
            "返回 true", "true", report_node="node1"),
        sql("确认指定 node_id 的 node_maximum 已更新", PORT1,
            "SELECT node_count::text || '|' || node_maximum::text || '|' || coalesce(force_nodeid::text,'NULL') || '|' || seq_state::text FROM fdd.mmr_global_sequence WHERE seq_name='%s'::regclass" % REF,
            "返回 3|{1:1,2:2,3:32678}|NULL|d", "3|{1:1,2:2,3:32678}|NULL|d", report_node="node1"),
        sql("按文档强制分离 node3", PORT1, "SELECT fdd.part_node('node3',true,true)",
            "返回 part node node3 successful", "part node node3 successful", report_node="node1", timeout=90),
        sql("按文档删除已强制分离的 node3", PORT1, "SELECT fdd.drop_node('node3',true)",
            "返回 drop node node3 successful", "drop node node3 successful", report_node="node1", timeout=60),
        sql("确认删除后的 node3 node_id 记录在 force_nodeid", PORT1,
            "SELECT (force_nodeid::text LIKE '%%{3}%%')::text FROM fdd.mmr_global_sequence WHERE seq_name='%s'::regclass" % REF,
            "返回 true", "true", report_node="node1"),
        sql("按文档更新强制分离 node_id 的指定序列值", PORT1,
            "SELECT fdd.set_global_seq(ARRAY['%s'::regclass],NULL,3,3267876)::text" % REF,
            "返回 true", "true", report_node="node1"),
        sql("确认强制节点值更新后从 force_nodeid 移除", PORT1,
            "SELECT node_count::text || '|' || node_maximum::text || '|' || force_nodeid::text || '|' || seq_state::text FROM fdd.mmr_global_sequence WHERE seq_name='%s'::regclass" % REF,
            "返回 3|{1:1,2:2,3:3267876}|{}|d", "3|{1:1,2:2,3:3267876}|{}|d", report_node="node1"),
    ],
    "teardown": "fixture 以 immediate 停止临时 node1/node2/node3 并删除整个目录；不修改共享 mmr 环境。",
}
