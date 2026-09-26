from framework.assertions import command_succeeds, output_contains_text
from suites.mmr.cases.conflict.delete_missing import setup
from suites.mmr.cases.node_management.create_group import (PSQL, create_node,
                                                            init_instance, psql,
                                                            shell)


ROOT = "/tmp/fbase_regress_mmr_forward_{run_id}"
NODE134, NODE135, PUBLISHER = ROOT + "/node134", ROOT + "/node135", ROOT + "/publisher_c"
PORT134, PORT135, PORT_C = "15447", "15448", "15446"
TABLE = "fbase_r_forward_{run_id}"
SUB = "fbase_r_forward_sub_{run_id}"
PUB = "fbase_r_forward_pub_{run_id}"
DSN134 = "host=127.0.0.1 port=%s user=postgres dbname=postgres" % PORT134


def command(title, port, statement, expected, assertion=command_succeeds(),
            timeout=30, report_node="node134"):
    step = shell(title, psql(port, statement), expected, assertion, timeout=timeout)
    step["display_sql"] = statement
    step["report_node"] = report_node
    return step


def query(title, port, statement, expected, *markers, **options):
    return command(title, port, statement, expected,
                   output_contains_text(*(markers or (expected,))),
                   options.get("timeout", 30), options.get("report_node", "node134"))


def wait_count(title, port, condition, expected, report_node):
    probe = "SELECT count(*)::text FROM public.%s WHERE %s" % (TABLE, condition)
    script = (
        "for i in $(seq 1 20); do v=$(%s -X -At -h 127.0.0.1 -p %s -U postgres "
        "-d postgres -c %r); test \"$v\" = %r && { %s; exit 0; }; sleep 1; done; "
        "%s; exit 1" %
        (PSQL, port, probe, expected, psql(port, probe), psql(port, probe)))
    step = shell(title, script, "20 秒内返回 %s" % expected,
                 command_succeeds(), timeout=25)
    step["display_sql"] = probe
    step["report_node"] = report_node
    return step


CASE = {
    "id": "mmr.forwarding.ordinary_logical", "name": "普通逻辑复制数据经多活订阅转发",
    "document": "多活功能测试文档.md", "section": "9.2", "group": "forwarding",
    "fixtures": ["cluster", {"type": "isolated_mmr_node_creation", "data_dir": ROOT}],
    "test_topology": {
        "summary": "节点数=3；node134/node135 为 two_phase=false 的隔离 MMR 成员，C 为普通逻辑发布端。",
        "nodes": [
            {"name": "node134", "role": "MMR primary", "host": "127.0.0.1", "port": PORT134, "data_dir": NODE134},
            {"name": "node135", "role": "MMR primary", "host": "127.0.0.1", "port": PORT135, "data_dir": NODE135},
            {"name": "C", "role": "普通逻辑发布端", "host": "127.0.0.1", "port": PORT_C, "data_dir": PUBLISHER},
        ],
        "relations": ["MMR 多活: node134 <-> node135", "普通逻辑复制: C -> node135"],
    },
    "requirements": {"clusters": ["mmr"], "plugins": ["fdd_mmr"],
                     "writable_node": True, "node": "mmr:mmr1"},
    "evidence_nodes": ["mmr:mmr1"],
    "prerequisites": [
        "隔离 node134/node135 以 two_phase=false 创建，满足默认复制集存量刷新约束。",
        "普通发布端 C 和所有 MMR 数据目录均位于本轮 /tmp 测试目录，结束后整体删除。",
    ],
    "steps": [
        setup(init_instance("初始化 node134", NODE134, PORT134)),
        setup(create_node(PORT134, "node134", two_phase=False)),
        setup(command("在 node134 创建文档测试表", PORT134,
                      "CREATE TABLE public.%s(id int PRIMARY KEY,name text)" % TABLE,
                      "返回 CREATE TABLE")),
        setup(command("创建 g1 多活集群", PORT134,
                      "SELECT fdd.create_group('g1')", "返回 node group create successful")),
        setup(init_instance("初始化 node135", NODE135, PORT135)),
        setup(create_node(PORT135, "node135", two_phase=False)),
        setup(command("以 all 模式将 node135 加入 g1", PORT135,
                      "SELECT fdd.join_group('g1','%s',true,'all','table_exist_error')" % DSN134,
                      "返回 node join to group complete finished", timeout=90, report_node="node135")),
        setup(init_instance("初始化普通逻辑发布端 C", PUBLISHER, PORT_C)),
        setup(command("在 C 创建文档测试表", PORT_C,
                      "CREATE TABLE public.%s(id int PRIMARY KEY,name text)" % TABLE,
                      "返回 CREATE TABLE", report_node="C")),
        setup(command("在 C 创建普通发布", PORT_C,
                      "CREATE PUBLICATION %s FOR TABLE public.%s" % (PUB, TABLE),
                      "返回 CREATE PUBLICATION", report_node="C")),
        command("在 node135 写入本地多活基线数据", PORT135,
                "INSERT INTO public.%s VALUES(1,'mmr')" % TABLE,
                "返回 INSERT 0 1", report_node="node135"),
        wait_count("确认基线数据已由多活同步到 node134", PORT134, "id=1", "1", "node134"),
        command("在 node135 创建来自 C 的普通订阅", PORT135,
                "CREATE SUBSCRIPTION %s CONNECTION 'host=127.0.0.1 port=%s user=postgres dbname=postgres' "
                "PUBLICATION %s WITH(copy_data=false)" % (SUB, PORT_C, PUB),
                "返回 CREATE SUBSCRIPTION", report_node="node135"),
        command("在 C 写入未启用转发的数据", PORT_C,
                "INSERT INTO public.%s VALUES(2,'ordinary-no-forward')" % TABLE,
                "返回 INSERT 0 1", report_node="C"),
        wait_count("确认 node135 收到普通订阅数据", PORT135, "id=2", "1", "node135"),
        query("确认未配置转发时 node134 不接收普通订阅来源", PORT134,
              "SELECT count(*)::text FROM public.%s WHERE id=2" % TABLE,
              "返回 0", "0"),
        command("按文档配置普通订阅来源转发", PORT134,
                "SELECT fdd.alter_forward_subs((SELECT sub_id FROM fdd.mmr_subscription "
                "WHERE origin_node_id=2 AND target_node_id=1), ARRAY['%s'])::text" % SUB,
                "返回 true"),
        query("确认 MMR 订阅元数据记录普通订阅名", PORT134,
              "SELECT forward_origins::text FROM fdd.mmr_subscription "
              "WHERE origin_node_id=2 AND target_node_id=1", "返回 {%s}" % SUB, "{%s}" % SUB),
        command("在 C 写入启用转发后的数据", PORT_C,
                "INSERT INTO public.%s VALUES(3,'ordinary-forward')" % TABLE,
                "返回 INSERT 0 1", report_node="C"),
        wait_count("确认 node135 收到转发测试数据", PORT135, "id=3", "1", "node135"),
        wait_count("确认 node134 收到经 MMR 转发的普通订阅数据", PORT134, "id=3", "1", "node134"),
        command("按文档清空普通订阅来源转发配置", PORT134,
                "SELECT fdd.alter_forward_subs((SELECT sub_id FROM fdd.mmr_subscription "
                "WHERE origin_node_id=2 AND target_node_id=1), NULL)::text",
                "返回 true"),
        command("在 C 写入清空转发后的数据", PORT_C,
                "INSERT INTO public.%s VALUES(4,'ordinary-stop')" % TABLE,
                "返回 INSERT 0 1", report_node="C"),
        wait_count("确认 node135 仍接收普通订阅数据", PORT135, "id=4", "1", "node135"),
        query("确认 node134 在清空配置后不再接收普通订阅来源", PORT134,
              "SELECT count(*)::text FROM public.%s WHERE id=4" % TABLE,
              "返回 0", "0"),
    ],
    "teardown": "fixture 以 immediate 停止 node134、node135 和 C，并删除整个临时目录；其中包含测试表、普通订阅、发布、MMR 元数据和复制槽。",
}
