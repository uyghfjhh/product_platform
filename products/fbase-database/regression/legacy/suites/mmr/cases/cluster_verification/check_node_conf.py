from framework.assertions import command_succeeds, output_contains_text
from suites.mmr.cases.conflict.delete_missing import setup
from suites.mmr.cases.node_management.create_group import (PSQL, create_node,
                                                            init_instance, psql,
                                                            shell)


ROOT = "/tmp/fbase_regress_mmr_check_node_conf_{run_id}"
NODE134, NODE135 = ROOT + "/node134", ROOT + "/node135"
PORT134, PORT135 = "15641", "15642"
DSN134 = "host=127.0.0.1 port=%s user=postgres dbname=postgres" % PORT134
TABLE = "fbase_regress_mmr_skip_{run_id}"
TABLE_REF = "public.%s" % TABLE
PROBE = "fbase_regress_mmr_skip_probe_{run_id}"


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


HEALTH_SQL = (
    "SELECT count(*)::text,bool_and(nodestate='ACTIVE')::text,"
    "bool_and(real_nodestate='ACTIVE')::text,bool_and(is_abnormal='OK')::text "
    "FROM fdd.show_node_info(true,false)")


CASE = {
    "id": "mmr.cluster_verification.check_node_conf_table_exclusion",
    "name": "多活集群校验配置表跳过表结构校验",
    "document": "多活功能测试文档.md", "section": "5.2 测试一",
    "group": "cluster_verification",
    "fixtures": ["cluster", {"type": "isolated_mmr_node_creation", "data_dir": ROOT}],
    "requirements": {"plugins": ["fdd_mmr"], "writable_node": True, "node": "mmr:mmr1"},
    "evidence_nodes": ["mmr:mmr1"],
    "test_topology": {
        "summary": "节点数=2；node134/node135 为 two_phase=false 的隔离 MMR 主节点。",
        "nodes": [
            {"name": "node134", "role": "MMR primary", "host": "127.0.0.1", "port": PORT134, "data_dir": NODE134},
            {"name": "node135", "role": "MMR primary", "host": "127.0.0.1", "port": PORT135, "data_dir": NODE135},
        ],
        "relations": ["MMR 多活: node134 <-> node135", "物理流复制: 无"],
    },
    "prerequisites": [
        "文档流程要求所有 MMR 节点 two_phase=false；两个隔离成员均按该前提创建。",
        "两个成员的校验配置表初始为空，测试表及元数据均随临时目录销毁。",
    ],
    "steps": [
        setup(init_instance("初始化 node134", NODE134, PORT134)),
        setup(create_node(PORT134, "node134", two_phase=False)),
        setup(command("在 node134 创建 all join 所需同构探针表", PORT134,
                      "CREATE TABLE public.%s(id integer PRIMARY KEY)" % PROBE,
                      "返回 CREATE TABLE")),
        setup(command("在 node134 创建 g1", PORT134,
                      "SELECT fdd.create_group('g1')", "返回 node group create successful")),
        setup(init_instance("初始化 node135", NODE135, PORT135)),
        setup(create_node(PORT135, "node135", two_phase=False)),
        setup(command("以 all 模式将 node135 加入 g1", PORT135,
                      "SELECT fdd.join_group('g1','%s',true,'all','table_exist_error')" % DSN134,
                      "返回 node join to group complete finished", timeout=90, report_node="node135")),
        query("确认隔离集群初始健康", PORT134, HEALTH_SQL,
              "返回 2|true|true|true", "2", "true"),
        query("确认 node134 校验配置表为空", PORT134,
              "SELECT count(*)::text FROM fdd.mmr_check_node_conf", "返回 0", "0"),
        command("在 node134 创建 text 类型测试表", PORT134,
                "CREATE TABLE %s(id integer PRIMARY KEY,str text)" % TABLE_REF,
                "返回 CREATE TABLE"),
        command("在 node135 创建 integer 类型测试表", PORT135,
                "CREATE TABLE %s(id integer PRIMARY KEY,str integer)" % TABLE_REF,
                "返回 CREATE TABLE", report_node="node135"),
        query("展示表结构不一致导致的集群校验错误", PORT134,
              "SELECT nodeid,nodename,is_abnormal,detail FROM fdd.show_node_info(true,false) "
              "WHERE is_abnormal='ERR_TABLE_NOSAMPLE' AND detail LIKE '%%%s%%'" % TABLE,
              "显示 ERR_TABLE_NOSAMPLE 和测试表名", "ERR_TABLE_NOSAMPLE", TABLE),
        command("在 node134 配置跳过该测试表", PORT134,
                "SELECT fdd.alter_mmr_check_node_conf(ARRAY['%s'])::text" % TABLE_REF,
                "返回 true"),
        command("在 node135 配置跳过该测试表", PORT135,
                "SELECT fdd.alter_mmr_check_node_conf(ARRAY['%s'])::text" % TABLE_REF,
                "返回 true", report_node="node135"),
        query("确认 node134 已记录跳过配置", PORT134,
              "SELECT local_exclude_tables::text FROM fdd.mmr_check_node_conf",
              "返回 {%s}" % TABLE_REF, "{%s}" % TABLE_REF),
        command("执行文档要求的异步复制集处理", PORT134,
                "SELECT fdd.replication_set_async_execute(true)", "SQL 执行成功", timeout=60),
        query("确认跳过配置后集群恢复健康", PORT134, HEALTH_SQL,
              "返回 2|true|true|true", "2", "true"),
        command("清空 node134 临时校验配置", PORT134,
                "TRUNCATE TABLE fdd.mmr_check_node_conf", "返回 TRUNCATE TABLE"),
        command("清空 node135 临时校验配置", PORT135,
                "TRUNCATE TABLE fdd.mmr_check_node_conf", "返回 TRUNCATE TABLE", report_node="node135"),
        command("删除 node134 测试表", PORT134,
                "DROP TABLE IF EXISTS %s" % TABLE_REF, "返回 DROP TABLE"),
        command("删除 node135 测试表", PORT135,
                "DROP TABLE IF EXISTS %s" % TABLE_REF, "返回 DROP TABLE", report_node="node135"),
    ],
    "teardown": "fixture 以 immediate 停止 node134/node135 并删除整个临时目录，清理异构表、复制槽、订阅和 MMR 元数据。",
}
