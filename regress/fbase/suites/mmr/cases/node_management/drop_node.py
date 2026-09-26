from framework.assertions import command_succeeds, output_contains
from framework.steps import command_step
from suites.mmr.cases.node_management.create_group import (PSQL, create_node,
                                                            init_instance, psql)


ROOT = "/tmp/fbase_regress_mmr_drop_node_{run_id}"
SEED, OBSERVER, TARGET = ROOT + "/seed", ROOT + "/observer", ROOT + "/target"
SEED_PORT, OBSERVER_PORT, TARGET_PORT = "15465", "15466", "15467"
DSN = "host=127.0.0.1 port=%s user=postgres dbname=postgres" % SEED_PORT


def shell(title, script, expected, assertion=command_succeeds(), timeout=60):
    return command_step(title, ["sh", "-ec", script], expected, assertion,
                        node="mmr:mmr1", timeout=timeout)


def setup(step):
    step["report"] = False
    return step


def value(title, port, sql, expected):
    return shell(title, "%s -X -At -F '|' -h 127.0.0.1 -p %s -U postgres -d postgres -c %r" %
                 (PSQL, port, sql), "输出 %s" % expected, output_contains(expected))


def init_node(data, port, name):
    return [init_instance("初始化 %s 隔离节点" % name, data, port),
            setup(shell("将 %s 的分离追增超时设为 60 秒" % port,
                  "%s -X -v ON_ERROR_STOP=1 -h 127.0.0.1 -p %s -U postgres -d postgres -c %r; /usr/local/fbase15.15/bin/pg_ctl -D %s reload" %
                  (PSQL, port, "ALTER SYSTEM SET fdd.part_catchup_timeout = '60s'", data), "ALTER SYSTEM 和 reload 成功")),
            create_node(port, name)]


def join(data, port, name):
    return [*init_node(data, port, name), shell("以 all 模式将 %s 加入 g1" % name,
            psql(port, "SELECT fdd.join_group('g1','%s',true,'all','table_exist_error')" % DSN),
            "返回 node join to group complete finished", timeout=60)]


def wait_count(title, port, count):
    query = "%s -X -At -h 127.0.0.1 -p %s -U postgres -d postgres -c 'SELECT count(*) FROM drop_probe'" % (PSQL, port)
    return setup(shell(title, "for i in $(seq 1 20); do test \"$(%s)\" = %s && exit 0; sleep 1; done; exit 1" % (query, count),
                       "20 秒内收到 %s 行复制数据" % count, timeout=25))


CASE = {
    "id": "mmr.node_management.drop_node", "name": "多活已分离节点删除",
    "document": "多活功能测试文档.md", "section": "2.5", "group": "node_management",
    "fixtures": ["cluster", {"type": "isolated_mmr_node_creation", "data_dir": ROOT}],
    "requirements": {"clusters": ["mmr"], "plugins": ["fdd_mmr"], "writable_node": True, "node": "mmr:mmr1"},
    "evidence_nodes": ["mmr:mmr1"],
    "prerequisites": ["node101、node102 和待删除 node1 均为临时多活成员；删除前先按文档完成 PARTED。"],
    "steps": [
        *init_node(SEED, SEED_PORT, "node101"),
        shell("建组前创建带主键的复制测试表", psql(SEED_PORT, "CREATE TABLE drop_probe(id int PRIMARY KEY)"), "返回 CREATE TABLE"),
        shell("创建 g1 集群", psql(SEED_PORT, "SELECT fdd.create_group('g1')"), "返回 node group create successful"),
        *join(OBSERVER, OBSERVER_PORT, "node102"), *join(TARGET, TARGET_PORT, "node1"),
        setup(shell("三个成员分别写入复制确认数据", psql(SEED_PORT, "INSERT INTO drop_probe VALUES(1)") + "; " + psql(OBSERVER_PORT, "INSERT INTO drop_probe VALUES(2)") + "; " + psql(TARGET_PORT, "INSERT INTO drop_probe VALUES(3)"), "返回三次 INSERT 0 1")),
        wait_count("确认 node101 收到三端复制数据", SEED_PORT, "3"), wait_count("确认 node102 收到三端复制数据", OBSERVER_PORT, "3"), wait_count("确认 node1 收到三端复制数据", TARGET_PORT, "3"),
        shell("按文档先分离 node1", psql(SEED_PORT, "SELECT fdd.part_node('node1',true,true)"), "返回 part node node1 successful", timeout=90),
        value("确认删除前 node1 为 PARTED", SEED_PORT, "SELECT node_state::text FROM fdd.mmr_node WHERE node_name='node1'", "PARTED"),
        shell("按文档删除已分离节点 node1", psql(SEED_PORT, "SELECT fdd.drop_node('node1',true)"), "返回 drop node node1 successful", timeout=60),
        value("确认 node101 元数据不再存在 node1", SEED_PORT, "SELECT count(*)::text FROM fdd.mmr_node WHERE node_name='node1'", "0"),
        value("确认 node102 元数据不再存在 node1", OBSERVER_PORT, "SELECT count(*)::text FROM fdd.mmr_node WHERE node_name='node1'", "0"),
        value("确认剩余两个成员均 ACTIVE", SEED_PORT, "SELECT count(*)::text,bool_and(node_state='ACTIVE')::text FROM fdd.mmr_node", "2|true"),
    ],
    "teardown": "fixture 以 immediate 停止三个隔离实例并删除整个临时目录。",
}
