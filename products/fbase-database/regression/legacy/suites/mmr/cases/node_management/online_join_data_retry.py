from framework.assertions import command_fails, command_succeeds, output_contains_text
from suites.mmr.cases.conflict.delete_missing import query, setup
from suites.mmr.cases.node_management.create_group import (PSQL, create_node,
                                                            init_instance, psql, shell)


ROOT = "/tmp/fbase_regress_mmr_online_join_data_retry_{run_id}"
SOURCE, JOINER = ROOT + "/source", ROOT + "/joiner"
SOURCE_PORT, JOINER_PORT = "15529", "15530"
DSN = "host=127.0.0.1 port=%s user=postgres dbname=postgres" % SOURCE_PORT
DOC_ROOT = "/home/postgres/fly_dev/postgresql_for_fbase_dev/contrib/fdd_mmr_dev/fdd_mmr/doc/转测文档/必测用例/在线join测试用例"
CREATE, DROP, INSERT = DOC_ROOT + "/create_table.sql", DOC_ROOT + "/drop_table.sql", DOC_ROOT + "/insert_data.sql"
PGBENCH = PSQL.rsplit("/", 1)[0] + "/pgbench"
PGBENCH_SQL = DOC_ROOT + "/pgbench_test.sql"
WORKLOAD_PID = ROOT + "/source-pgbench.pid"
ONLINE_JOIN_SETTINGS = ("max_worker_processes = 18\n"
                        "max_logical_replication_workers = 12\n"
                        "max_sync_workers_per_subscription = 2\n"
                        "max_replication_slots = 64\n"
                        "max_wal_senders = 64\n")


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


def show_online_settings(label, port):
    return query("展示 %s 的冲突 join 容量参数" % label, port,
                 "SELECT current_setting('max_worker_processes') AS max_worker_processes,current_setting('max_logical_replication_workers') AS max_logical_replication_workers,current_setting('max_sync_workers_per_subscription') AS max_sync_workers_per_subscription,current_setting('max_replication_slots') AS max_replication_slots,current_setting('max_wal_senders') AS max_wal_senders",
                 "返回 18、12、2、64、64", "18", "12", "2", "64")


def first_join():
    step = shell("测试四：data-only 模式在在线业务下遇存量冲突后完成 5 次检查并报错",
                 psql(JOINER_PORT, "SELECT fdd.join_group('g1','%s',true,'data-only','table_exist_error')" % DSN),
                 "返回 after 5 times test 的产品错误；节点保留 JOIN_START",
                 command_fails("after 5 times test"), timeout=360)
    step["display_sql"] = (
        "-- node134：持续执行文档 pgbench_test.sql 在线业务\n"
        "\\! %s -n -r -P 1 -h 127.0.0.1 -p %s -U postgres -f %s postgres -c 2 -j 2 -T 45 -R 40 &\n"
        "-- node138\nSELECT fdd.join_group('g1','%s',true,'data-only','table_exist_error');" %
        (PGBENCH, SOURCE_PORT, PGBENCH_SQL, DSN))
    return step


def documented_drop():
    step = shell("测试四第 4 步：按原文执行 drop_table.sql", document_script(JOINER_PORT, DROP), "所有 31 张表均删除成功", timeout=60)
    step["display_sql"] = "\\i %s" % DROP
    step["continue_on_failure"] = True
    return step


def retry_join():
    return shell("测试四第 5 步：删除冲突表后以 data-only 模式重新 join",
                 psql(JOINER_PORT, "SELECT fdd.join_group('g1','%s',true,'data-only','table_exist_error')" % DSN),
                 "返回 node join to group complete finished", command_succeeds(), timeout=180)


def same_count(table):
    statement = "SELECT count(*)::text FROM public.%s" % table
    script = (
        "source=$(%s -X -At -h 127.0.0.1 -p %s -U postgres -d postgres -c %r); "
        "joiner=$(%s -X -At -h 127.0.0.1 -p %s -U postgres -d postgres -c %r); "
        "for i in $(seq 1 120); do test \"$source\" = \"$joiner\" && break; sleep 1; "
        "source=$(%s -X -At -h 127.0.0.1 -p %s -U postgres -d postgres -c %r); "
        "joiner=$(%s -X -At -h 127.0.0.1 -p %s -U postgres -d postgres -c %r); done; "
        "printf 'node134 | %%s\\nnode138 | %%s\\n' \"$source\" \"$joiner\"; test \"$source\" = \"$joiner\"" %
        (PSQL, SOURCE_PORT, statement, PSQL, JOINER_PORT, statement,
         PSQL, SOURCE_PORT, statement, PSQL, JOINER_PORT, statement))
    step = shell("等待追增完成并确认两端 %s 行数一致" % table, script,
                 "node134 与 node138 的行数相同", output_contains_text("node134", "node138"),
                 timeout=150)
    step["display_sql"] = "-- node134\n%s;\n-- node138\n%s;" % (statement, statement)
    return step


CASE = {
    "id": "mmr.node_management.online_join_data_retry", "name": "[LONG-TIME] data-only 在线 join 中断后的恢复及重入", "document": "多活功能测试文档.md", "section": "2.3.2.1 测试四", "group": "node_management", "known_issue": "D-017", "default_enabled": False,
    "fixtures": ["cluster", {"type": "isolated_mmr_node_creation", "data_dir": ROOT}],
    "requirements": {"clusters": ["mmr"], "plugins": ["fdd_mmr"], "writable_node": True, "node": "mmr:mmr1"}, "evidence_nodes": ["mmr:mmr1"],
    "prerequisites": ["源端按文档 31 表和缩小存量运行随附 pgbench_test.sql；候选端按测试四预建同构表并写入缩小存量。", "31 表均冲突时将临时同步槽容量设为 64；首次 join 必须等待产品完成自身的 5 次检测，随后严格执行原文 drop_table.sql 和 data-only 重试。"],
    "steps": [
        setup(init_instance("按在线冲突容量初始化源节点 node134", SOURCE, SOURCE_PORT, ONLINE_JOIN_SETTINGS)), setup(create_node(SOURCE_PORT, "node134")), setup(shell("按文档创建源端 31 表", document_script(SOURCE_PORT, CREATE), "返回 31 张 CREATE TABLE")), setup(shell("按文档写入缩小存量", document_script(SOURCE_PORT, INSERT, True), "每张表写入 3 行缩小存量")), setup(shell("创建 g1 集群", psql(SOURCE_PORT, "SELECT fdd.create_group('g1')"), "返回 node group create successful")), setup(start_workload()),
        setup(init_instance("按在线冲突容量初始化 data-only 重入节点 node138", JOINER, JOINER_PORT, ONLINE_JOIN_SETTINGS)), setup(shell("测试四第 2 步：按文档预建同构 31 表并写入缩小存量", document_script(JOINER_PORT, CREATE) + "; " + document_script(JOINER_PORT, INSERT, True), "返回 31 张 CREATE TABLE 并写入缩小存量")), setup(create_node(JOINER_PORT, "node138")),
        show_online_settings("node134", SOURCE_PORT), show_online_settings("node138", JOINER_PORT), first_join(), query("确认 data-only 中断后 node138 为 JOIN_START", JOINER_PORT, "SELECT node_state FROM fdd.mmr_node WHERE node_name='node138'", "返回 JOIN_START", "JOIN_START"), documented_drop(), retry_join(), same_count("test1"), same_count("test30_repeat_repeat_repeat_repeat_repeat_repeat_repeat_repeat"),
    ],
    "teardown": "以 immediate 停止 node134/node138 临时实例；递归删除临时目录，连同 31 张测试表、序列、MMR 元数据、订阅、复制槽和中断 join 状态一并删除。",
}
