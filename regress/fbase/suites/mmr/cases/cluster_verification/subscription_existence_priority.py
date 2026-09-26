"""MMR functional document 5.1.2: subscription existence takes priority."""

from framework.assertions import command_succeeds, output_contains_text
from suites.mmr.cases.node_management.create_group import (PSQL, create_node,
                                                            init_instance, psql,
                                                            shell)


ROOT = "/tmp/fbase_regress_mmr_subscription_priority_{run_id}"
NODE1, NODE2 = ROOT + "/node1", ROOT + "/node2"
NODE1_PORT, NODE2_PORT = "15631", "15632"
NODE1_DSN = "host=127.0.0.1 port=%s user=postgres dbname=postgres" % NODE1_PORT
SUBSCRIPTION = "fmmr_postgres_g1_node2"


def setup(step):
    step["report"] = False
    return step


def sql(title, port, statement, expected, *markers, **options):
    step = shell(
        title,
        "%s -X -P pager=off -h 127.0.0.1 -p %s -U postgres -d postgres -c %r" %
        (PSQL, port, statement),
        expected,
        output_contains_text(*(markers or (expected,))),
        timeout=options.get("timeout", 30),
    )
    step["display_sql"] = statement
    step["report_node"] = options.get("report_node")
    return step


CASE = {
    "id": "mmr.cluster_verification.subscription_existence_priority",
    "name": "多活集群校验订阅不存在优先级",
    "document": "多活功能测试文档.md",
    "section": "5.1.2",
    "group": "cluster_verification",
    "fixtures": ["cluster", {"type": "isolated_mmr_node_creation", "data_dir": ROOT}],
    "requirements": {"clusters": ["mmr"], "plugins": ["fdd_mmr"],
                     "writable_node": True, "node": "mmr:mmr1"},
    "evidence_nodes": ["mmr:mmr1"],
    "test_topology": {
        "summary": "节点数=2；node1/node2 为对等可写 MMR 主节点；无物理备库、无独立普通逻辑复制。",
        "nodes": [
            {"name": "node1", "role": "MMR primary", "host": "127.0.0.1", "port": NODE1_PORT, "data_dir": NODE1},
            {"name": "node2", "role": "MMR primary", "host": "127.0.0.1", "port": NODE2_PORT, "data_dir": NODE2},
        ],
        "relations": ["MMR 多活: node1 <-> node2（双向复制）", "物理流复制: 无"],
    },
    "prerequisites": [
        "按文档创建独立 node1/node2 两节点 g1；测试实例位于 /tmp，结束后立即删除。",
        "文档要求删除系统 MMR 订阅；操作仅作用于临时 node1，不触碰共享 mmr 环境。",
    ],
    "steps": [
        setup(init_instance("初始化 node1", NODE1, NODE1_PORT)),
        setup(create_node(NODE1_PORT, "node1")),
        # all 模式必须有可订阅的默认复制集对象；这是文档展示状态前
        # 已建立的两节点集群所隐含的建组前置。
        setup(shell("创建两节点集群的默认复制集测试表",
                    psql(NODE1_PORT, "CREATE TABLE public.subscription_priority_probe(id int PRIMARY KEY)"),
                    "返回 CREATE TABLE")),
        setup(shell("按文档在 node1 创建 g1", psql(NODE1_PORT, "SELECT fdd.create_group('g1')"),
                    "返回 node group create successful")),
        setup(init_instance("初始化 node2", NODE2, NODE2_PORT)),
        setup(create_node(NODE2_PORT, "node2")),
        setup(shell("按文档以 all 模式将 node2 加入 g1",
                    psql(NODE2_PORT, "SELECT fdd.join_group('g1','%s',true,'all','table_exist_error')" % NODE1_DSN),
                    "返回 node join to group complete finished", timeout=90)),
        sql("确认 MMR 元数据中存在文档订阅", NODE1_PORT,
            "SELECT sub_id::text,sub_name FROM fdd.mmr_subscription", "显示 fmmr_postgres_g1_node2", SUBSCRIPTION,
            report_node="node1"),
        sql("确认 pg_subscription 中存在文档订阅", NODE1_PORT,
            "SELECT oid::text,subdbid::text,subname FROM pg_subscription", "显示 fmmr_postgres_g1_node2", SUBSCRIPTION,
            report_node="node1"),
        sql("按文档删除 MMR 订阅", NODE1_PORT,
            "DROP SUBSCRIPTION %s" % SUBSCRIPTION, "返回 DROP SUBSCRIPTION", "DROP SUBSCRIPTION",
            report_node="node1", timeout=60),
        sql("按文档展示订阅不存在和复制槽错误", NODE1_PORT,
            "SELECT nodeid,nodename,is_abnormal,detail FROM fdd.show_node_info(true,false) ORDER BY nodeid,is_abnormal,detail",
            "显示 ERR_SUB_EXIST 和两条 ERR_SLOT_EXIST", "ERR_SUB_EXIST", "ERR_SLOT_EXIST", report_node="node1"),
        sql("确认订阅不存在后不继续校验订阅属性和运行状态", NODE1_PORT,
            "WITH r AS (SELECT is_abnormal FROM fdd.show_node_info(true,false)) SELECT concat(count(*) FILTER (WHERE is_abnormal='ERR_SUB_EXIST'),'|',count(*) FILTER (WHERE is_abnormal='ERR_SLOT_EXIST'),'|',count(*) FILTER (WHERE is_abnormal IN ('ERR_SUB_STATE','ERR_SUB_RUN','ERR_SUB_TABLE_STATE'))) FROM r",
            "返回 1|2|0", "1|2|0", report_node="node1"),
    ],
    "teardown": "fixture 以 immediate 停止临时 node1/node2 并删除整个目录；其中的订阅、复制槽和 MMR 元数据随之清除。",
}
