from framework.assertions import command_succeeds, output_contains_text
from framework.steps import command_step
from suites.mmr.cases.node_management.create_group import (PSQL, create_node,
                                                            init_instance, psql)


ROOT = "/tmp/fbase_regress_mmr_wait_join_{run_id}"
SEED, JOINER = ROOT + "/seed", ROOT + "/joiner"
SEED_PORT, JOINER_PORT = "15468", "15469"
DSN = "host=127.0.0.1 port=%s user=postgres dbname=postgres" % SEED_PORT


def shell(title, script, expected, assertion=command_succeeds(), timeout=60):
    return command_step(title, ["sh", "-ec", script], expected, assertion,
                        node="mmr:mmr1", timeout=timeout)


CASE = {
    "id": "mmr.node_management.wait_for_join_completion",
    "name": "异步节点加入进度等待与完成状态",
    "document": "多活功能测试文档.md", "section": "2.6", "group": "node_management",
    "fixtures": ["cluster", {"type": "isolated_mmr_node_creation", "data_dir": ROOT}],
    "requirements": {"clusters": ["mmr"], "plugins": ["fdd_mmr"], "writable_node": True, "node": "mmr:mmr1"},
    "evidence_nodes": ["mmr:mmr1"],
    "prerequisites": ["两个临时实例预加载 fdd_mmr；建组节点包含带主键表，使异步 join 有有效订阅复制集。"],
    "steps": [
        init_instance("初始化建组节点 node134", SEED, SEED_PORT), create_node(SEED_PORT, "node134"),
        shell("建组前创建供异步 join 同步的测试表", psql(SEED_PORT, "CREATE TABLE wait_join_probe(id int PRIMARY KEY)"), "返回 CREATE TABLE"),
        shell("创建 g1 集群", psql(SEED_PORT, "SELECT fdd.create_group('g1')"), "返回 node group create successful"),
        init_instance("初始化异步加入节点 node135", JOINER, JOINER_PORT), create_node(JOINER_PORT, "node135"),
        shell("按文档发起 wait_for_completion=false 的异步 join", psql(JOINER_PORT, "SELECT fdd.join_group('g1','%s',false,'all','table_exist_error')" % DSN), "返回异步 join 请求成功"),
        shell("按文档等待并显示异步 join 全部阶段", psql(JOINER_PORT, "SELECT fdd.wait_for_join_completion(true)"),
              "输出 async、CREATED、JOIN_START、DATASYNC、CATCHUP、ACTIVE 和 success",
              output_contains_text("The join process is async.", "node state is CREATED.", "node state is JOIN_START.", "node state is DATASYNC.", "node state is CATCHUP.", "node state is ACTIVE.", "success"), timeout=90),
        shell("确认 join 完成后再次等待返回 failed", psql(JOINER_PORT, "SELECT fdd.wait_for_join_completion(true)"),
              "输出 local node join is done 与 failed", output_contains_text("local node join is done.", "failed")),
    ],
    "teardown": "fixture 以 immediate 停止两个隔离实例并删除整个临时目录。",
}
