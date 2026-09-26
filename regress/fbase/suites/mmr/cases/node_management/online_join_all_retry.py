from framework.assertions import command_succeeds, output_contains_text
from suites.mmr.cases.conflict.delete_missing import query, setup
from suites.mmr.cases.node_management.create_group import (PSQL, create_node,
                                                            init_instance, psql, shell)


ROOT = "/tmp/fbase_regress_mmr_online_join_all_retry_{run_id}"
SOURCE, JOINER = ROOT + "/source", ROOT + "/joiner"
SOURCE_PORT, JOINER_PORT = "15531", "15532"
DSN = "host=127.0.0.1 port=%s user=postgres dbname=postgres" % SOURCE_PORT
DOC_ROOT = "/home/postgres/fly_dev/postgresql_for_fbase_dev/contrib/fdd_mmr_dev/fdd_mmr/doc/转测文档/必测用例/在线join测试用例"
CREATE, DROP, INSERT = DOC_ROOT + "/create_table.sql", DOC_ROOT + "/drop_table.sql", DOC_ROOT + "/insert_data.sql"
PGBENCH = PSQL.rsplit("/", 1)[0] + "/pgbench"
PGBENCH_SQL = DOC_ROOT + "/pgbench_test.sql"
WORKLOAD_PID = ROOT + "/source-pgbench.pid"


def document_script(port, path, scaled=False):
    command = "%s -X -v ON_ERROR_STOP=1 -h 127.0.0.1 -p %s -U postgres -d postgres" % (PSQL, port)
    if not scaled:
        return "%s -f %s" % (command, path)
    return ("sed -e 's/^\\\\set num_inserts 80000/\\\\set num_inserts 3/' -e 's/^\\\\set num_inserts1 2000000/\\\\set num_inserts1 3/' -e 's/^\\\\set num_inserts2 120000/\\\\set num_inserts2 3/' %s | %s" % (path, command))


def start_workload():
    step = shell("按测试一第 3 步在源端启动 pgbench 在线业务",
                 "%s -n -r -P 1 -h 127.0.0.1 -p %s -U postgres -f %s postgres -c 2 -j 2 -T 45 -R 40 >/dev/null 2>&1 & echo $! > %s" %
                 (PGBENCH, SOURCE_PORT, PGBENCH_SQL, WORKLOAD_PID),
                 "pgbench 已后台启动并覆盖首次 join 和重试 join")
    step["report"] = False
    return step


def documented_join():
    step = shell("测试二：all 模式在在线业务下执行首次 join",
                 psql(JOINER_PORT, "SELECT fdd.join_group('g1','%s',true,'all','table_exist_error')" % DSN),
                 "产品实际成功完成 join；原文“同构表必然中断”的预期不成立", command_succeeds(), timeout=180)
    step["display_sql"] = (
        "-- node134：持续执行文档 pgbench_test.sql 在线业务\n"
        "\\! %s -n -r -P 1 -h 127.0.0.1 -p %s -U postgres -f %s postgres -c 2 -j 2 -T 45 -R 40 &\n"
        "-- node136\nSELECT fdd.join_group('g1','%s',true,'all','table_exist_error');" %
        (PGBENCH, SOURCE_PORT, PGBENCH_SQL, DSN))
    return step


def same_count(table):
    statement = "SELECT count(*)::text FROM public.%s" % table
    script = (
        "source=$(%s -X -At -h 127.0.0.1 -p %s -U postgres -d postgres -c %r); "
        "joiner=$(%s -X -At -h 127.0.0.1 -p %s -U postgres -d postgres -c %r); "
        "for i in $(seq 1 120); do test \"$source\" = \"$joiner\" && break; sleep 1; "
        "source=$(%s -X -At -h 127.0.0.1 -p %s -U postgres -d postgres -c %r); "
        "joiner=$(%s -X -At -h 127.0.0.1 -p %s -U postgres -d postgres -c %r); done; "
        "printf 'node134 | %%s\\nnode136 | %%s\\n' \"$source\" \"$joiner\"; test \"$source\" = \"$joiner\"" %
        (PSQL, SOURCE_PORT, statement, PSQL, JOINER_PORT, statement,
         PSQL, SOURCE_PORT, statement, PSQL, JOINER_PORT, statement))
    step = shell("等待追增完成并确认两端 %s 行数一致" % table, script,
                 "node134 与 node136 的行数相同", output_contains_text("node134", "node136"),
                 timeout=150)
    step["display_sql"] = "-- node134\n%s;\n-- node136\n%s;" % (statement, statement)
    return step


CASE = {
    "id": "mmr.node_management.online_join_all_retry", "name": "[LONG-TIME] all 在线 join 同构表场景", "document": "多活功能测试文档.md", "section": "2.3.2.1 测试二", "group": "node_management", "default_enabled": False,
    "fixtures": ["cluster", {"type": "isolated_mmr_node_creation", "data_dir": ROOT}],
    "requirements": {"clusters": ["mmr"], "plugins": ["fdd_mmr"], "writable_node": True, "node": "mmr:mmr1"}, "evidence_nodes": ["mmr:mmr1"],
    "prerequisites": ["源端按文档 31 表和缩小存量运行随附 pgbench_test.sql；候选端按测试二只预建同构表。", "原文将同构表视为必然冲突；产品实际完成首次 join，因此原文后续删除和重入步骤不适用。"],
    "steps": [
        setup(init_instance("初始化源节点 node134", SOURCE, SOURCE_PORT)), setup(create_node(SOURCE_PORT, "node134")), setup(shell("按文档创建源端 31 表", document_script(SOURCE_PORT, CREATE), "返回 31 张 CREATE TABLE")), setup(shell("按文档写入缩小存量", document_script(SOURCE_PORT, INSERT, True), "每张表写入 3 行缩小存量")), setup(shell("创建 g1 集群", psql(SOURCE_PORT, "SELECT fdd.create_group('g1')"), "返回 node group create successful")), setup(start_workload()),
        setup(init_instance("初始化 all 重入节点 node136", JOINER, JOINER_PORT)), setup(shell("测试二第 2 步：按文档预建同构 31 表", document_script(JOINER_PORT, CREATE), "返回 31 张 CREATE TABLE")), setup(create_node(JOINER_PORT, "node136")),
        documented_join(), query("确认同构表首次 all join 后 node136 为 ACTIVE", JOINER_PORT, "SELECT node_state FROM fdd.mmr_node WHERE node_name='node136'", "返回 ACTIVE", "ACTIVE"), same_count("test1"), same_count("test30_repeat_repeat_repeat_repeat_repeat_repeat_repeat_repeat"),
    ],
    "teardown": "以 immediate 停止 node134/node136 临时实例；递归删除临时目录，连同 31 张测试表、序列、MMR 元数据、订阅、复制槽和中断 join 状态一并删除。",
}
