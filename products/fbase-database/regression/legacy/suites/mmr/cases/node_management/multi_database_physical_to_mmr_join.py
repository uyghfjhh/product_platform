from framework.assertions import command_succeeds, output_contains_text
from framework.steps import command_step
from suites.mmr.cases.conflict.delete_missing import setup, sql
from suites.mmr.cases.node_management.create_group import PGCTL, PGHOME, PSQL, psql, shell

ROOT = "/tmp/fbase_regress_mmr_multi_database_physical_{run_id}"
NODE34, NODE35, NODE37 = ROOT + "/node134", ROOT + "/node135", ROOT + "/node137"
PORT34, PORT35, PORT37 = "15577", "15578", "15579"
DATABASES = ("postgres", "test")
NAMES34, NAMES35, NAMES37 = ("node34", "test34"), ("node35", "test35"), ("node37", "test37")
GROUPS = ("g1", "group1")
TABLE = "multi_physical_{run_id}"

def db_psql(port, database, statement):
    return "%s -X -v ON_ERROR_STOP=1 -P pager=off -h 127.0.0.1 -p %s -U postgres -d %s -c %r" % (PSQL, port, database, statement)

def query_db(title, port, database, statement, expected, *markers, **options):
    step = command_step(title, ["sh", "-ec", db_psql(port, database, statement)], expected,
                        output_contains_text(*(markers or (expected,))), node="mmr:mmr1")
    step["display_sql"], step["report_node"] = statement, options.get("report_node")
    step["report_database"] = database
    return step

def wait_db(title, port, database, statement, expected, timeout=60):
    script = "for i in $(seq 1 55); do v=$(%s -X -At -h 127.0.0.1 -p %s -U postgres -d %s -c %r); test \"$v\" = %r && { echo \"$v\"; exit 0; }; sleep 1; done; exit 1" % (PSQL, port, database, statement, expected)
    step = sql(title, script, "55 秒内输出 %s" % expected, statement, timeout,
               report_node="node134" if port == PORT34 else "node137")
    step["report_database"] = database
    return step

def init_multi(title, data_dir, port):
    command = ("mkdir -p %s; %s/bin/initdb -D %s -U postgres --auth-local=trust --auth-host=trust; cp /home/postgres/license/license.dat %s/license.dat; printf \"\\nshared_preload_libraries = 'fdd_mmr'\\nfdd.running_databases = 'postgres,test'\\nwal_level = logical\\ntrack_commit_timestamp = on\\nmax_worker_processes = 18\\nmax_logical_replication_workers = 12\\nmax_sync_workers_per_subscription = 2\\nmax_replication_slots = 12\\nmax_wal_senders = 12\\nlisten_addresses = '127.0.0.1'\\nport = %s\\n\" >> %s/postgresql.conf; %s -D %s -l %s/start.log -w start" % (data_dir, PGHOME, data_dir, data_dir, port, data_dir, PGCTL, data_dir, data_dir))
    return setup(shell(title, command, "临时实例启动成功", timeout=60))

def prepare_node(title, data_dir, port, node_names, create_groups=False):
    actions = [db_psql(port, "postgres", "CREATE DATABASE test"),
               db_psql(port, "postgres", "CREATE EXTENSION fdd_mmr; CREATE EXTENSION fb_license"),
               db_psql(port, "test", "CREATE EXTENSION fdd_mmr; CREATE EXTENSION fb_license")]
    for database, node_name, group in zip(DATABASES, node_names, GROUPS):
        dsn = "host=127.0.0.1 port=%s user=postgres dbname=%s" % (port, database)
        statement = "SELECT fdd.create_node('%s','%s')" % (node_name, dsn)
        if create_groups:
            statement += "; SELECT fdd.create_group('%s')" % group
        actions.append(db_psql(port, database, statement))
    return setup(shell(title, "; ".join(actions), "两个数据库均创建扩展和本地节点", timeout=150))

def join_peer_steps():
    steps = []
    for database, group in zip(DATABASES, GROUPS):
        dsn = "host=127.0.0.1 port=%s user=postgres dbname=%s" % (PORT34, database)
        statement = "SELECT fdd.join_group('%s','%s',true,'all','table_exist_error')" % (group, dsn)
        step = command_step("按文档将 node135 的 %s 加入 %s" % (database, group), ["sh", "-ec", db_psql(PORT35, database, statement)], "返回 node join to group complete finished", command_succeeds(), node="mmr:mmr1", timeout=120, display_sql=statement, report_node="node135")
        step["report_database"] = database
        steps.append(step)
    return steps

def health_steps(database):
    state = "SELECT count(*)::text || '|' || bool_and(is_abnormal='OK')::text FROM fdd.show_node_info(true,false)"
    return [wait_db("等待 %s 的 node137 追增完成" % database, PORT34, database, state, "3|true"),
            query_db("确认 node137 的 %s 三成员均 ACTIVE" % database, PORT37, database, "SELECT count(*)::text,bool_and(node_state='ACTIVE')::text FROM fdd.mmr_node", "返回 3|true", "3", "true", report_node="node137")]

CASE = {
    "id": "mmr.node_management.multi_database_physical_to_mmr_join", "name": "多数据库物理备库转换后自动加入多活组",
    "document": "多活功能测试文档.md", "section": "9.8.3", "group": "node_management",
    "fixtures": ["cluster", {"type": "isolated_mmr_node_creation", "data_dir": ROOT}],
    "requirements": {"clusters": ["mmr"], "plugins": ["fdd_mmr"], "commands": ["fdd_mmr_join"], "writable_node": True, "node": "mmr:mmr1"}, "evidence_nodes": ["mmr:mmr1"],
    "test_topology": {"summary": "节点数=3；node134/node135 在 postgres、test 各有独立 MMR 组，node137 从 node134 的物理备库转换后自动加入两个组。", "nodes": [{"name":"node134","role":"双数据库 MMR primary","host":"127.0.0.1","port":PORT34,"data_dir":NODE34},{"name":"node135","role":"双数据库 MMR primary","host":"127.0.0.1","port":PORT35,"data_dir":NODE35},{"name":"node137","role":"物理备库 -> 双数据库 MMR primary","host":"127.0.0.1","port":PORT37,"data_dir":NODE37}], "relations":["MMR 多活: postgres: node34,node35,node37；test: test34,test35,test37","物理流复制: node134 -> node137（转换前）"]},
    "prerequisites": ["三个临时实例均在启动时配置 fdd.running_databases='postgres,test'；node135 在 node137 自动 join 期间不写入。"],
    "steps": [
        init_multi("初始化 node134 双数据库主库", NODE34, PORT34), prepare_node("在 node134 的两个数据库创建节点和组", NODE34, PORT34, NAMES34, True),
        setup(shell("在 node134 两个数据库创建文档测试表和初始数据", "; ".join(db_psql(PORT34, database, "CREATE TABLE public.%s(id serial PRIMARY KEY,data text); INSERT INTO public.%s(data) VALUES('seed1'),('seed2')" % (TABLE, TABLE)) for database in DATABASES), "两个数据库均创建表和初始数据")),
        init_multi("初始化 node135 双数据库对等节点", NODE35, PORT35), prepare_node("在 node135 的两个数据库创建本地节点", NODE35, PORT35, NAMES35), *join_peer_steps(),
        setup(shell("使用 pg_basebackup 创建 node137 物理备库", "mkdir -p %s; chmod 700 %s; /usr/local/fbase15.15/bin/pg_basebackup -h 127.0.0.1 -p %s -U postgres -D %s -R -X stream -c fast; cp %s/license.dat %s/license.dat; printf \"\\nport = %s\\nlisten_addresses = '127.0.0.1'\\n\" >> %s/postgresql.conf; %s -D %s -l %s/start.log -w start" % (NODE37,NODE37,PORT34,NODE37,NODE34,NODE37,PORT37,NODE37,PGCTL,NODE37,NODE37), "物理备库启动成功", timeout=90)),
        query_db("确认 node137 转换前为物理备库", PORT37, "postgres", "SELECT pg_is_in_recovery()", "返回 true", "t", report_node="node137"), setup(shell("按文档停止 node137 物理备库", "%s -D %s stop -m fast" % (PGCTL,NODE37), "已停止")),
        sql("按文档以两个 -d/-M/-L 执行 fdd_mmr_join", "/usr/local/fbase15.15/bin/fdd_mmr_join -U postgres -d postgres -d test -D %s -P 'host=127.0.0.1 port=%s user=postgres dbname=postgres' -p %s -M node37 -M test37 -L 'host=127.0.0.1 port=%s user=postgres dbname=postgres' -L 'host=127.0.0.1 port=%s user=postgres dbname=test' -A" % (NODE37,PORT34,PORT37,PORT37,PORT37), "fdd_mmr_join 成功完成", "fdd_mmr_join -U postgres -d postgres -d test -D %s -P 'host=node134 port=%s user=postgres dbname=postgres' -p %s -M node37 -M test37 -L 'host=node137 port=%s user=postgres dbname=postgres' -L 'host=node137 port=%s user=postgres dbname=test' -A" % (NODE37,PORT34,PORT37,PORT37,PORT37), 240, report_node="node137"),
        sql("按文档启动已转换的 node137 双数据库多活节点", "%s -D %s -l %s/start.log -w start" % (PGCTL,NODE37,NODE37), "服务器启动成功", "pg_ctl -D %s -l %s/start.log -w start" % (NODE37,NODE37), 60, report_node="node137"),
        query_db("确认 node137 转换后为可写节点", PORT37, "postgres", "SELECT pg_is_in_recovery()", "返回 false", "f", report_node="node137"),
        *[item for database in DATABASES for item in health_steps(database)],
        *[item for database in DATABASES for item in [sql("在 node134 的 %s 写入转换后业务数据" % database, db_psql(PORT34, database, "INSERT INTO public.%s(data) VALUES('after_join')" % TABLE), "返回 INSERT 0 1", "INSERT INTO public.%s(data) VALUES('after_join');" % TABLE, report_node="node134"), wait_db("等待 node137 的 %s 收到转换后业务数据" % database, PORT37, database, "SELECT count(*) FROM public.%s WHERE data='after_join'" % TABLE, "1")]],
    ],
    "teardown": "以 immediate 停止 node134/node135/node137 并删除临时目录，清理两个数据库的物理复制、MMR 订阅、槽和测试数据。",
}
