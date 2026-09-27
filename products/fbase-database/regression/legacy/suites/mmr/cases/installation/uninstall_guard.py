from framework.assertions import command_fails, output_contains_text
from suites.mmr.cases.conflict.delete_missing import query, setup, sql
from suites.mmr.cases.node_management.create_group import (PSQL, create_node,
                                                            init_instance, psql,
                                                            shell)


ROOT = "/tmp/fbase_regress_mmr_uninstall_guard_{run_id}"
NODE134, NODE135 = ROOT + "/node134", ROOT + "/node135"
NODE134_PORT, NODE135_PORT = "15534", "15535"
NODE134_DSN = "host=127.0.0.1 port=%s user=postgres dbname=postgres" % NODE134_PORT


def expected_failure(title, port, statement, expected, marker, report_node):
    step = shell(title, "%s -X -P pager=off -h 127.0.0.1 -p %s -U postgres -d postgres -c %r" %
                 (PSQL, port, statement), expected, command_fails(marker))
    step["display_sql"] = statement
    step["report_node"] = report_node
    return step


def configure_part_timeout(data_dir, port):
    return setup(shell(
        "将 %s 的分离追增超时设为 60 秒" % port,
        "%s -X -v ON_ERROR_STOP=1 -h 127.0.0.1 -p %s -U postgres -d postgres -c %r; "
        "/usr/local/fbase15.15/bin/pg_ctl -D %s reload" % (
            PSQL, port, "ALTER SYSTEM SET fdd.part_catchup_timeout = '60s'", data_dir),
        "ALTER SYSTEM 和 reload 成功"))


CASE = {
    "id": "mmr.installation.uninstall_guard",
    "name": "运行中多活节点禁止卸载及分离删除后的卸载",
    "document": "多活功能测试文档.md", "section": "10", "group": "installation",
    "fixtures": ["cluster", {"type": "isolated_mmr_node_creation", "data_dir": ROOT}],
    "requirements": {"clusters": ["mmr"], "plugins": ["fdd_mmr"],
                     "writable_node": True, "node": "mmr:mmr1"},
    "evidence_nodes": ["mmr:mmr1"],
    "test_topology": {
        "summary": "节点数=2；两端均为可写 MMR 主节点；无物理备库、无独立普通逻辑复制。",
        "nodes": [
            {"name": "node134", "role": "MMR primary", "host": "127.0.0.1",
             "port": NODE134_PORT, "data_dir": NODE134},
            {"name": "node135", "role": "MMR primary", "host": "127.0.0.1",
             "port": NODE135_PORT, "data_dir": NODE135},
        ],
        "relations": ["MMR 多活: node134 <-> node135，group=g1", "物理流复制: 无"],
    },
    "prerequisites": [
        "node134 与 node135 是本用例创建的双节点临时多活集群；两端均已安装 fdd_mmr。",
        "卸载只在已从集群删除的 node135 临时实例执行，fixture 结束时删除两个临时 PGDATA。",
    ],
    "steps": [
        setup(init_instance("初始化 node134", NODE134, NODE134_PORT)),
        configure_part_timeout(NODE134, NODE134_PORT),
        setup(create_node(NODE134_PORT, "node134")),
        setup(shell("建组前创建供默认复制集订阅的最小表",
                    psql(NODE134_PORT, "CREATE TABLE uninstall_probe(id int PRIMARY KEY)"),
                    "返回 CREATE TABLE")),
        setup(shell("创建 g1 集群", psql(NODE134_PORT, "SELECT fdd.create_group('g1')"),
                    "返回 node group create successful")),
        setup(init_instance("初始化 node135", NODE135, NODE135_PORT)),
        configure_part_timeout(NODE135, NODE135_PORT),
        setup(create_node(NODE135_PORT, "node135")),
        setup(shell("以 all 模式将 node135 加入 g1",
                    psql(NODE135_PORT, "SELECT fdd.join_group('g1','%s',true,'all','table_exist_error')" % NODE134_DSN),
                    "返回 node join to group complete finished", timeout=90)),
        query("展示运行中节点的本地多活元数据", NODE135_PORT,
              "SELECT node_id,set_mode,sub_repsets FROM fdd.mmr_local_node",
              "返回有效 node_id 和默认复制集", "d", "{g1}", report_node="node135"),
        expected_failure("按文档在运行中的 node135 卸载 fdd_mmr", NODE135_PORT,
                         "DROP EXTENSION fdd_mmr",
                         "执行失败，提示当前节点正在运行，需先分离并删除节点",
                         "当前节点正在运行", "node135"),
        query("确认卸载被拒绝后 fdd_mmr 扩展仍存在", NODE135_PORT,
              "SELECT extname FROM pg_extension WHERE extname='fdd_mmr'",
              "返回 fdd_mmr", "fdd_mmr", report_node="node135"),
        sql("按文档在 node134 分离 node135",
            psql(NODE134_PORT, "SELECT fdd.part_node('node135',true,true)"),
            "返回 part node node135 successful",
            "SELECT fdd.part_node('node135',true,true)", report_node="node134"),
        query("确认 node135 已处于 PARTED", NODE134_PORT,
              "SELECT node_state FROM fdd.mmr_node WHERE node_name='node135'",
              "返回 PARTED", "PARTED", report_node="node134"),
        sql("按文档在 node134 删除已分离 node135",
            psql(NODE134_PORT, "SELECT fdd.drop_node('node135',true)"),
            "返回 drop node node135 successful",
            "SELECT fdd.drop_node('node135',true)", report_node="node134"),
        query("确认 node134 元数据中已没有 node135", NODE134_PORT,
              "SELECT count(*) AS node135_count FROM fdd.mmr_node WHERE node_name='node135'",
              "返回 0", "0", report_node="node134"),
        sql("按文档在已删除节点 node135 卸载 fdd_mmr",
            psql(NODE135_PORT, "DROP EXTENSION fdd_mmr"), "返回 DROP EXTENSION",
            "DROP EXTENSION fdd_mmr", report_node="node135"),
        query("确认 node135 的 fdd_mmr 扩展已删除", NODE135_PORT,
              "SELECT count(*) AS fdd_mmr_count FROM pg_extension WHERE extname='fdd_mmr'",
              "返回 0", "0", report_node="node135"),
    ],
    "teardown": "以 immediate 停止 node134 与 node135 临时实例；递归删除两个 PGDATA，连同 g1 元数据、订阅、复制槽、扩展目录和启动日志一并删除。",
}
