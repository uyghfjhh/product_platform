from framework.assertions import command_succeeds, output_contains_text
from suites.mmr.cases.conflict.delete_missing import setup
from suites.mmr.cases.node_management.create_group import (PSQL, create_node,
                                                            init_instance, psql, shell)

ROOT = "/tmp/fbase_regress_mmr_schema_filter_{run_id}"
NODE134, NODE135 = ROOT + "/node134", ROOT + "/node135"
PORT134, PORT135 = "15651", "15652"
DSN134 = "host=127.0.0.1 port=%s user=postgres dbname=postgres" % PORT134
SCHEMA = "fbase_r_mmr_filter_{run_id}"
TABLE_REF = "%s.test" % SCHEMA
PROBE = "fbase_r_mmr_filter_probe_{run_id}"
SUB = "fmmr_postgres_g1_node134"


def command(title, port, statement, expected, assertion=command_succeeds(), timeout=30, report_node="node134"):
    step = shell(title, psql(port, statement), expected, assertion, timeout=timeout)
    step["display_sql"], step["report_node"] = statement, report_node
    return step


def query(title, port, statement, expected, *markers, **options):
    return command(title, port, statement, expected, output_contains_text(*(markers or (expected,))),
                   options.get("timeout", 30), options.get("report_node", "node134"))


def wait_count(title, port, expected, report_node):
    statement = "SELECT count(*)::text FROM %s" % TABLE_REF
    script = ("for i in $(seq 1 20); do v=$(%s -X -At -h 127.0.0.1 -p %s -U postgres -d postgres -c %r); "
              "test \"$v\" = %r && { %s; exit 0; }; sleep 1; done; %s; exit 1" %
              (PSQL, port, statement, expected, psql(port, statement), psql(port, statement)))
    step = shell(title, script, "20 秒内返回 %s" % expected, command_succeeds(), timeout=25)
    step["display_sql"], step["report_node"] = statement, report_node
    return step


CASE = {
    "id": "mmr.default_publication.schema_filtering", "name": "默认发布按 schema 过滤",
    "document": "多活功能测试文档.md", "section": "9.7", "group": "default_publication",
    "fixtures": ["cluster", {"type": "isolated_mmr_node_creation", "data_dir": ROOT}],
    "requirements": {"plugins": ["fdd_mmr"], "writable_node": True, "node": "mmr:mmr1"},
    "evidence_nodes": ["mmr:mmr1"],
    "test_topology": {"summary": "节点数=2；node134/node135 为 two_phase=false 的隔离 MMR 主节点。",
                      "nodes": [{"name":"node134","role":"MMR primary","host":"127.0.0.1","port":PORT134,"data_dir":NODE134},{"name":"node135","role":"MMR primary","host":"127.0.0.1","port":PORT135,"data_dir":NODE135}],
                      "relations": ["MMR 多活: node134 <-> node135", "物理流复制: 无"]},
    "prerequisites": ["默认复制集异步刷新要求所有成员 two_phase=false；测试在隔离双节点执行。"],
    "steps": [
        setup(init_instance("初始化 node134", NODE134, PORT134)), setup(create_node(PORT134, "node134", two_phase=False)),
        setup(command("创建 all join 所需同构探针表", PORT134, "CREATE TABLE public.%s(id int PRIMARY KEY)" % PROBE, "返回 CREATE TABLE")),
        setup(command("创建 g1", PORT134, "SELECT fdd.create_group('g1')", "返回 node group create successful")),
        setup(init_instance("初始化 node135", NODE135, PORT135)), setup(create_node(PORT135, "node135", two_phase=False)),
        setup(command("以 all 模式将 node135 加入 g1", PORT135, "SELECT fdd.join_group('g1','%s',true,'all','table_exist_error')" % DSN134, "返回 node join to group complete finished", timeout=90, report_node="node135")),
        setup(command("在 node134 创建未过滤 schema 和表", PORT134, "CREATE SCHEMA %s; CREATE TABLE %s(id int PRIMARY KEY,name name)" % (SCHEMA, TABLE_REF), "返回 CREATE SCHEMA、CREATE TABLE")),
        setup(command("在 node135 创建同构 schema 和表", PORT135, "CREATE SCHEMA %s; CREATE TABLE %s(id int PRIMARY KEY,name name)" % (SCHEMA, TABLE_REF), "返回 CREATE SCHEMA、CREATE TABLE", report_node="node135")),
        command("按文档执行默认复制集异步处理", PORT134, "SELECT fdd.replication_set_async_execute(true)", "SQL 执行成功", timeout=60),
        command("发布端插入首行", PORT134, "INSERT INTO %s VALUES(2,'name')" % TABLE_REF, "返回 INSERT 0 1"),
        wait_count("确认 node135 收到首行", PORT135, "1", "node135"),
        command("按文档在发布端设置 schema 过滤", PORT134, "ALTER SYSTEM SET fdd.exclude_schema = 'fdd,%s'" % SCHEMA, "ALTER SYSTEM 成功"),
        command("重载发布端过滤配置", PORT134, "SELECT pg_reload_conf()", "返回 t"),
        query("确认发布端过滤 GUC 已生效", PORT134, "SHOW fdd.exclude_schema", "返回 fdd,%s" % SCHEMA, "fdd,%s" % SCHEMA),
        command("按文档刷新 node135 默认订阅发布", PORT135, "ALTER SUBSCRIPTION %s REFRESH PUBLICATION" % SUB, "返回 ALTER SUBSCRIPTION", report_node="node135"),
        command("发布端插入过滤后的第二行", PORT134, "INSERT INTO %s VALUES(4,'22')" % TABLE_REF, "返回 INSERT 0 1"),
        query("确认发布端保留两行", PORT134, "SELECT count(*)::text FROM %s" % TABLE_REF, "返回 2", "2"),
        query("确认订阅端仍只有首行", PORT135, "SELECT count(*)::text FROM %s" % TABLE_REF, "返回 1", "1", report_node="node135"),
    ],
    "teardown": "fixture 以 immediate 停止两个隔离成员并删除整个目录，清理 schema、订阅、复制槽和临时配置。",
}
