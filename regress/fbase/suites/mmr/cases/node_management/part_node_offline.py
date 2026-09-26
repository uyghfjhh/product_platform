from framework.assertions import command_fails, command_succeeds, output_contains
from framework.steps import command_step
from suites.mmr.cases.node_management.create_group import (PSQL, create_node,
                                                            init_instance, psql)


ROOT = "/tmp/fbase_regress_mmr_part_offline_{run_id}"
SEED, OBSERVER, FORCE_TRUE, FORCE_FALSE = (ROOT + "/seed", ROOT + "/observer",
                                            ROOT + "/force_true", ROOT + "/force_false")
SEED_PORT, OBSERVER_PORT, TRUE_PORT, FALSE_PORT = "15461", "15462", "15463", "15464"
DSN = "host=127.0.0.1 port=%s user=postgres dbname=postgres" % SEED_PORT


def shell(title, script, expected, assertion=command_succeeds(), timeout=60):
    return command_step(title, ["sh", "-ec", script], expected, assertion,
                        node="mmr:mmr1", timeout=timeout)


def value(title, port, sql, expected):
    return shell(title, "%s -X -At -F '|' -h 127.0.0.1 -p %s -U postgres -d postgres -c %r" %
                 (PSQL, port, sql), "输出 %s" % expected, output_contains(expected))


def configure_timeout(data, port):
    return shell("将 %s 的分离追增超时设为 60 秒" % port,
                 "%s -X -v ON_ERROR_STOP=1 -h 127.0.0.1 -p %s -U postgres -d postgres -c %r; /usr/local/fbase15.15/bin/pg_ctl -D %s reload" %
                 (PSQL, port, "ALTER SYSTEM SET fdd.part_catchup_timeout = '60s'", data),
                 "ALTER SYSTEM 和 reload 成功")


def join(data, port, name):
    return [init_instance("初始化 %s 隔离节点" % name, data, port), configure_timeout(data, port),
            create_node(port, name), shell("以 all 模式将 %s 加入 g1" % name,
            psql(port, "SELECT fdd.join_group('g1','%s',true,'all','table_exist_error')" % DSN),
            "返回 node join to group complete finished", timeout=60)]


def stop(data, name):
    return shell("按文档停止待分离节点 %s" % name,
                 "/usr/local/fbase15.15/bin/pg_ctl -D %s stop -m fast" % data,
                 "返回 server stopped")


def start(data, name):
    return shell("按文档启动节点 %s 观察本地状态" % name,
                 "/usr/local/fbase15.15/bin/pg_ctl -D %s -l %s/restart.log -w start" % (data, data),
                 "返回 server started")


CASE = {
    "id": "mmr.node_management.part_node_offline",
    "name": "停机节点 force 参数分离及恢复状态",
    "document": "多活功能测试文档.md",
    "section": "2.4 force=true 停机节点,2.4 force=false 停机节点",
    "group": "node_management",
    "fixtures": ["cluster", {"type": "isolated_mmr_node_creation", "data_dir": ROOT}],
    "requirements": {"clusters": ["mmr"], "plugins": ["fdd_mmr"], "writable_node": True, "node": "mmr:mmr1"},
    "evidence_nodes": ["mmr:mmr1"],
    "prerequisites": ["node101 与 node102 保持在线；两次待分离节点均在停止前成功加入 g1。"],
    "steps": [
        init_instance("初始化建组节点 node101", SEED, SEED_PORT), configure_timeout(SEED, SEED_PORT), create_node(SEED_PORT, "node101"),
        shell("在建组前创建供 join 使用的最小测试表", psql(SEED_PORT, "CREATE TABLE part_probe(id int PRIMARY KEY)"), "返回 CREATE TABLE"),
        shell("创建 g1 集群", psql(SEED_PORT, "SELECT fdd.create_group('g1')"), "返回 node group create successful"),
        *join(OBSERVER, OBSERVER_PORT, "node102"), *join(FORCE_TRUE, TRUE_PORT, "node1_true"),
        stop(FORCE_TRUE, "node1_true"),
        shell("按文档对停机节点执行 force=true 分离", psql(SEED_PORT, "SELECT fdd.part_node('node1_true',false,true)"), "返回 part node node1_true successful", timeout=60),
        value("确认 node101 记录停机 node1_true 为 PARTED", SEED_PORT, "SELECT node_state::text FROM fdd.mmr_node WHERE node_name='node1_true'", "PARTED"),
        value("确认 node102 同步看到停机 node1_true 为 PARTED", OBSERVER_PORT, "SELECT node_state::text FROM fdd.mmr_node WHERE node_name='node1_true'", "PARTED"),
        start(FORCE_TRUE, "node1_true"),
        value("确认恢复后 node1_true 本地仍显示 ACTIVE", TRUE_PORT, "SELECT node_state::text FROM fdd.mmr_node WHERE node_name='node1_true'", "ACTIVE"),
        shell("按文档在恢复节点本地完成 force=true 分离", psql(TRUE_PORT, "SELECT fdd.part_node('node1_true',false,true)"), "返回 part node node1_true successful", timeout=60),
        value("确认 node1_true 本地最终为 PARTED", TRUE_PORT, "SELECT node_state::text FROM fdd.mmr_node WHERE node_name='node1_true'", "PARTED"),
        *join(FORCE_FALSE, FALSE_PORT, "node1_false"), stop(FORCE_FALSE, "node1_false"),
        shell("按文档对停机节点执行 force=false 分离并确认失败", psql(SEED_PORT, "SELECT fdd.part_node('node1_false',false,false)"), "返回连接失败错误", command_fails("connect failed"), timeout=60),
        value("确认 node101 保持 node1_false 为 ACTIVE", SEED_PORT, "SELECT node_state::text FROM fdd.mmr_node WHERE node_name='node1_false'", "ACTIVE"),
        value("确认 node102 保持 node1_false 为 ACTIVE", OBSERVER_PORT, "SELECT node_state::text FROM fdd.mmr_node WHERE node_name='node1_false'", "ACTIVE"),
        start(FORCE_FALSE, "node1_false"),
        value("确认恢复后 node1_false 本地仍为 ACTIVE", FALSE_PORT, "SELECT node_state::text FROM fdd.mmr_node WHERE node_name='node1_false'", "ACTIVE"),
    ],
    "teardown": "fixture 以 immediate 停止四个隔离实例并删除整个临时目录。",
}
