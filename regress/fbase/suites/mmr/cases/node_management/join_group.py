from framework.assertions import command_fails, command_succeeds, output_contains
from framework.steps import command_step
from suites.mmr.cases.node_management.create_group import (PGHOME, PSQL, create_node,
                                                            init_instance, psql, shell)


ROOT = "/tmp/fbase_regress_mmr_join_group_{run_id}"
PRIMARY, JOINER, RETRY = ROOT + "/primary", ROOT + "/joiner", ROOT + "/retry"
PRIMARY_PORT, JOINER_PORT, RETRY_PORT = "15454", "15455", "15456"
TARGET_DSN = "host=127.0.0.1 port=%s user=postgres dbname=postgres" % PRIMARY_PORT


def shell(title, script, expected, assertion=command_succeeds(), timeout=30,
          continue_on_failure=False):
    return command_step(title, ["sh", "-ec", script], expected, assertion,
                        node="mmr:mmr1", timeout=timeout,
                        continue_on_failure=continue_on_failure)


def scalar_step(title, port, sql, expected):
    return shell(title, "%s -X -At -F '|' -h 127.0.0.1 -p %s -U postgres -d postgres -c %r" %
                 (PSQL, port, sql), "输出 %s" % expected, output_contains(expected))


CASE = {
    "id": "mmr.node_management.join_group",
    "name": "[LONG-TIME] 多活节点加入集群及中断重入",
    "document": "多活功能测试文档.md", "section": "2.3.1", "group": "node_management",
    "known_issue": "D-017", "default_enabled": False,
    "fixtures": ["cluster", {"type": "isolated_mmr_node_creation", "data_dir": ROOT}],
    "requirements": {"clusters": ["mmr"], "plugins": ["fdd_mmr"],
                     "writable_node": True, "node": "mmr:mmr1"},
    "evidence_nodes": ["mmr:mmr1"],
    "prerequisites": [
        "三个隔离实例都使用 fdd_mmr 预加载、logical WAL 和有效 license。",
        "主节点建组前创建 test 表和一行数据；retry 节点创建同名表和冲突主键，专用于文档的 data-only 中断重入。",
    ],
    "steps": [
        init_instance("初始化建组节点 node134", PRIMARY, PRIMARY_PORT),
        create_node(PRIMARY_PORT, "node134"),
        shell("在 node134 创建文档冲突测试表和数据", psql(PRIMARY_PORT, "CREATE TABLE test(id int PRIMARY KEY,name text); INSERT INTO test VALUES(1,'a')"), "返回 CREATE TABLE、INSERT 0 1"),
        shell("在 node134 创建 g1 集群", psql(PRIMARY_PORT, "SELECT fdd.create_group('g1')"), "返回 node group create successful"),
        init_instance("初始化正常加入节点 node135", JOINER, JOINER_PORT),
        create_node(JOINER_PORT, "node135"),
        shell("按文档以 all 模式将 node135 加入 g1", psql(JOINER_PORT, "SELECT fdd.join_group('g1','%s',true,'all','table_exist_error')" % TARGET_DSN), "返回 node join to group complete finished", timeout=60),
        scalar_step("确认 node135 加入后为 ACTIVE 且获得 test 存量数据", JOINER_PORT, "SELECT (SELECT node_state::text FROM fdd.mmr_node WHERE node_name='node135'),(SELECT count(*)::text FROM test)", "ACTIVE|1"),
        init_instance("初始化中断重入节点 node136", RETRY, RETRY_PORT),
        shell("在 node136 创建同名表和冲突数据", psql(RETRY_PORT, "CREATE TABLE test(id int PRIMARY KEY,name text); INSERT INTO test VALUES(1,'conflict')"), "返回 CREATE TABLE、INSERT 0 1"),
        create_node(RETRY_PORT, "node136"),
        shell("按文档以 data-only 加入并触发存量复制失败", psql(RETRY_PORT, "SELECT fdd.join_group('g1','%s',true,'data-only','table_exist_error')" % TARGET_DSN), "返回 copy table [test] failed", command_fails("copy table [test] failed"), timeout=180, continue_on_failure=True),
        scalar_step("确认失败后 node136 保持 JOIN_START", RETRY_PORT, "SELECT node_state::text FROM fdd.mmr_node WHERE node_name='node136'", "JOIN_START"),
        shell("按文档以 none 模式重入加入 g1", psql(RETRY_PORT, "SELECT fdd.join_group('g1','%s',true,'none','table_exist_error')" % TARGET_DSN), "返回 node join to group complete finished", timeout=60),
        scalar_step("确认重入后 node136 变为 ACTIVE 并保留本地冲突数据", RETRY_PORT, "SELECT (SELECT node_state::text FROM fdd.mmr_node WHERE node_name='node136'),(SELECT name FROM test WHERE id=1)", "ACTIVE|conflict"),
        scalar_step("确认主节点元数据中三个节点均为 ACTIVE", PRIMARY_PORT, "SELECT count(*)::text,bool_and(node_state='ACTIVE')::text FROM fdd.mmr_node", "3|true"),
    ],
    "teardown": "fixture 以 immediate 停止三个隔离实例并删除整个临时目录。",
}
