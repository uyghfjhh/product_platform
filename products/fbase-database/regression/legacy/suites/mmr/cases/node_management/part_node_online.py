from framework.assertions import command_succeeds, output_contains
from framework.steps import command_step
from suites.mmr.cases.node_management.create_group import (PSQL, create_node,
                                                            init_instance, psql)


ROOT = "/tmp/fbase_regress_mmr_part_online_{run_id}"
SEED, OBSERVER, FORCE_TRUE, FORCE_FALSE = (ROOT + "/seed", ROOT + "/observer",
                                            ROOT + "/force_true", ROOT + "/force_false")
SEED_PORT, OBSERVER_PORT, TRUE_PORT, FALSE_PORT = "15457", "15458", "15459", "15460"
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


def wait_probe(title, port, expected):
    query = "%s -X -At -h 127.0.0.1 -p %s -U postgres -d postgres -c 'SELECT count(*) FROM part_probe'" % (PSQL, port)
    return setup(shell(title, "for i in $(seq 1 20); do test \"$(%s)\" = %s && exit 0; sleep 1; done; exit 1" % (query, expected),
                       "20 秒内收到 %s 行复制数据" % expected, timeout=25))


def configure_part_timeout(data, port):
    return setup(shell("将 %s 的分离追增超时设为 60 秒" % port,
                 "%s -X -v ON_ERROR_STOP=1 -h 127.0.0.1 -p %s -U postgres -d postgres -c %r; "
                 "/usr/local/fbase15.15/bin/pg_ctl -D %s reload" %
                 (PSQL, port, "ALTER SYSTEM SET fdd.part_catchup_timeout = '60s'", data),
                 "ALTER SYSTEM 和 reload 成功"))


def join(data, port, name):
    return [init_instance("初始化 %s 隔离节点" % name, data, port), configure_part_timeout(data, port), create_node(port, name),
            shell("以 all 模式将 %s 加入 g1" % name,
                  psql(port, "SELECT fdd.join_group('g1','%s',true,'all','table_exist_error')" % DSN),
                  "返回 node join to group complete finished", timeout=60)]


CASE = {
    "id": "mmr.node_management.part_node_online",
    "name": "在线节点 force 参数分离及状态传播",
    "document": "多活功能测试文档.md",
    "section": "2.4 force=true 活跃节点,2.4 force=false 活跃节点",
    "group": "node_management",
    "fixtures": ["cluster", {"type": "isolated_mmr_node_creation", "data_dir": ROOT}],
    "requirements": {"clusters": ["mmr"], "plugins": ["fdd_mmr"],
                     "writable_node": True, "node": "mmr:mmr1"},
    "evidence_nodes": ["mmr:mmr1"],
    "prerequisites": ["四个隔离实例均使用 fdd_mmr；node101 建组，node102 始终保留为在线观察成员。"],
    "steps": [
        init_instance("初始化建组节点 node101", SEED, SEED_PORT), configure_part_timeout(SEED, SEED_PORT), create_node(SEED_PORT, "node101"),
        shell("在建组前创建供 join 订阅的最小测试表", psql(SEED_PORT, "CREATE TABLE part_probe(id int PRIMARY KEY)"), "返回 CREATE TABLE"),
        shell("创建 g1 集群", psql(SEED_PORT, "SELECT fdd.create_group('g1')"), "返回 node group create successful"),
        *join(OBSERVER, OBSERVER_PORT, "node102"),
        *join(FORCE_TRUE, TRUE_PORT, "node1_true"),
        value("确认 force=true 分离前三个成员 ACTIVE", SEED_PORT,
              "SELECT count(*)::text,bool_and(node_state='ACTIVE')::text FROM fdd.mmr_node", "3|true"),
        shell("在 node101 写入分离前的复制确认数据", psql(SEED_PORT, "INSERT INTO part_probe VALUES(1)"), "返回 INSERT 0 1"),
        shell("在 node102 写入分离前的复制确认数据", psql(OBSERVER_PORT, "INSERT INTO part_probe VALUES(2)"), "返回 INSERT 0 1"),
        shell("在 node1_true 写入分离前的复制确认数据", psql(TRUE_PORT, "INSERT INTO part_probe VALUES(3)"), "返回 INSERT 0 1"),
        wait_probe("确认 node101 收到三个成员的复制数据", SEED_PORT, "3"),
        wait_probe("确认 node102 收到三个成员的复制数据", OBSERVER_PORT, "3"),
        wait_probe("确认 node1_true 收到三个成员的复制数据", TRUE_PORT, "3"),
        shell("按文档对在线节点执行 force=true 分离", psql(SEED_PORT, "SELECT fdd.part_node('node1_true',true,true)"), "返回 part node node1_true successful"),
        value("确认 node101 记录 node1_true 为 PARTED", SEED_PORT,
              "SELECT node_state::text FROM fdd.mmr_node WHERE node_name='node1_true'", "PARTED"),
        value("确认 node102 同步看到 node1_true 为 PARTED", OBSERVER_PORT,
              "SELECT node_state::text FROM fdd.mmr_node WHERE node_name='node1_true'", "PARTED"),
        value("确认被分离节点本地看到自身 PARTED", TRUE_PORT,
              "SELECT node_state::text FROM fdd.mmr_node WHERE node_name='node1_true'", "PARTED"),
        *join(FORCE_FALSE, FALSE_PORT, "node1_false"),
        value("确认 force=false 分离前在线成员 ACTIVE", SEED_PORT,
              "SELECT node_state::text FROM fdd.mmr_node WHERE node_name='node1_false'", "ACTIVE"),
        shell("按文档对在线节点执行 force=false 分离", psql(SEED_PORT, "SELECT fdd.part_node('node1_false',true,false)"), "返回 part node node1_false successful"),
        value("确认 node101 记录 node1_false 为 PARTED", SEED_PORT,
              "SELECT node_state::text FROM fdd.mmr_node WHERE node_name='node1_false'", "PARTED"),
        value("确认 node102 同步看到 node1_false 为 PARTED", OBSERVER_PORT,
              "SELECT node_state::text FROM fdd.mmr_node WHERE node_name='node1_false'", "PARTED"),
        value("确认 force=false 被分离节点本地看到自身 PARTED", FALSE_PORT,
              "SELECT node_state::text FROM fdd.mmr_node WHERE node_name='node1_false'", "PARTED"),
    ],
    "teardown": "fixture 以 immediate 停止四个隔离实例并删除整个临时目录。",
}
