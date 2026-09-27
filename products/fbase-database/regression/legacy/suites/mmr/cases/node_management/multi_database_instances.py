"""Document 2.3.2.2's four-database two-peer active-cluster flow.

The supplied create.sql creates the three extra databases but only creates
tables in its initial database.  We execute that same table-definition body
in each extra database, rather than replacing the document workload.
"""

from framework.assertions import command_succeeds, output_contains_text
from suites.mmr.cases.conflict.delete_missing import setup
from suites.mmr.cases.node_management.create_group import PGHOME, PSQL, shell


ROOT = "/tmp/fbase_regress_mmr_multi_database_{run_id}"
NODE134, NODE135 = ROOT + "/node134", ROOT + "/node135"
PORT134, PORT135 = "15561", "15562"
DOC_ROOT = ("/home/postgres/fly_dev/postgresql_for_fbase_dev/contrib/fdd_mmr/"
            "doc/转测文档/必测用例/多数据库实例测试用例")
CREATE_SQL = DOC_ROOT + "/create.sql"
INSERT_SQL = DOC_ROOT + "/insert_data.sql"
DATABASES = [
    "postgres", "test",
    "abcdefghijklmnopqrstuvwxyz_1234567890_1234567890_1234567890_db1",
    "abcdefghijklmnopqrstuvwxyz_1234567890_1234567890_1234567890_db2",
]
NODE_NAMES = ["node1", "node1", "node1", "nodea"]
JOINER_NODE_NAMES = ["node2", "node2", "node2", "nodeb"]
GROUPS = [
    "group_postgres", "group_test",
    "group_abcdefghijklmnopqrstuvwxyz_0123456789_01234567890_dbname1",
    "group_abcdefghijklmnopqrstuvwxyz_0123456789_01234567890_dbname2",
]
RUNNING_DATABASES = ",".join(DATABASES)


def psql(port, database, statement):
    return ("%s -X -v ON_ERROR_STOP=1 -P pager=off -h 127.0.0.1 -p %s "
            "-U postgres -d %s -c %r" % (PSQL, port, database, statement))


def visible(title, command, expected, assertion=command_succeeds(), timeout=90,
            statement=None, report_node=None):
    step = shell(title, command, expected, assertion, timeout=timeout)
    step["display_sql"] = statement
    step["report_node"] = report_node
    return step


def init_instance(data_dir, port):
    command = (
        "mkdir -p {data}; {home}/bin/initdb -D {data} -U postgres "
        "--auth-local=trust --auth-host=trust; "
        "cp /home/postgres/license/license.dat {data}/license.dat; "
        "printf \"\\nshared_preload_libraries = 'fdd_mmr'\\n"
        "fdd.running_databases = '{databases}'\\nwal_level = logical\\n"
        "track_commit_timestamp = on\\nmax_worker_processes = 18\\n"
        "max_logical_replication_workers = 12\\n"
        "max_sync_workers_per_subscription = 2\\nmax_replication_slots = 12\\n"
        "max_wal_senders = 12\\nlisten_addresses = '127.0.0.1'\\nport = {port}\\n\" "
        ">> {data}/postgresql.conf; "
        "{home}/bin/pg_ctl -D {data} -l {data}/start.log -w start"
    ).format(data=data_dir, home=PGHOME, databases=RUNNING_DATABASES, port=port)
    return setup(shell("初始化四数据库临时节点", command, "初始化并启动成功", timeout=60))


def create_document_databases(port):
    # create.sql's first three statements create the extra databases; its
    # remaining body is reused verbatim for each database as required by 2.3.2.2.
    command = ["%s -f %s" % (psql_file_base(port, "postgres"), CREATE_SQL)]
    for database in DATABASES[1:]:
        command.append("tail -n +5 %s | %s" %
                       (CREATE_SQL, psql_file_base(port, database)))
    for database in DATABASES:
        command.append(psql(port, database, "CREATE EXTENSION fdd_mmr; CREATE EXTENSION fb_license"))
    return setup(shell("按文档创建四个数据库、31 表和四套扩展", "; ".join(command),
                       "四个数据库均完成文档表定义和扩展安装", timeout=180))


def psql_file_base(port, database):
    return ("%s -X -v ON_ERROR_STOP=1 -h 127.0.0.1 -p %s -U postgres -d %s" %
            (PSQL, port, database))


def seed_document_data(port):
    return setup(shell(
        "按文档 insert_data.sql 写入四个数据库的存量",
        "; ".join("%s -f %s" % (psql_file_base(port, database), INSERT_SQL)
                  for database in DATABASES),
        "四个数据库均完成文档存量写入", timeout=180))


def create_groups():
    statements = []
    for database, node_name, group in zip(DATABASES, NODE_NAMES, GROUPS):
        dsn = "host=127.0.0.1 port=%s user=postgres dbname=%s" % (PORT134, database)
        statements.append(psql(PORT134, database,
                               "SELECT fdd.create_node('%s','%s'); "
                               "SELECT fdd.create_group('%s');" %
                               (node_name, dsn, group)))
    return setup(shell("在 node134 的四个数据库分别创建节点和集群", "; ".join(statements),
                       "四个数据库均创建本地节点和集群", timeout=180))


def create_joiner_nodes():
    statements = []
    for database, node_name in zip(DATABASES, JOINER_NODE_NAMES):
        dsn = "host=127.0.0.1 port=%s user=postgres dbname=%s" % (PORT135, database)
        statements.append(psql(PORT135, database,
                               "SELECT fdd.create_node('%s','%s')" %
                               (node_name, dsn)))
    return setup(shell("为 node135 的四个数据库创建 join_group 所需本地节点", "; ".join(statements),
                       "四个数据库均创建本地节点", timeout=180))


def join_groups():
    steps = []
    for database, group in zip(DATABASES, GROUPS):
        dsn = "host=127.0.0.1 port=%s user=postgres dbname=%s" % (PORT134, database)
        statement = ("SELECT fdd.join_group('%s','%s',true,'all',"
                     "'table_exist_error')" % (group, dsn))
        steps.append(visible(
            "按文档将 node135 的 %s 以 all 模式加入对应集群" % database,
            psql(PORT135, database, statement), "返回 node join to group complete finished",
            timeout=180, statement=statement, report_node="node135"))
    return steps


def check_cluster(port, node_name, database):
    statement = ("SELECT format('%s|%s|%s',count(*)::text,"
                 "bool_and(is_abnormal='OK')::text,"
                 "bool_and(nodestate='ACTIVE')::text) AS cluster_state "
                 "FROM fdd.show_node_info(true,false)")
    command = psql(port, database, statement)
    return visible("校验 %s 的 %s 多活集群" % (node_name, database),
                   command, "返回 2|true|true",
                   output_contains_text("2|true|true"), statement=statement,
                   report_node=node_name)


def t31_flow(database):
    create = ("SELECT fdd.run_on_all_nodes('CREATE TABLE t31(id int PRIMARY KEY, "
              "name1 text, name2 text)'); SELECT fdd.replication_set_async_execute(true)")
    insert = "INSERT INTO t31 VALUES(1,'a1','a2')"
    compact_query = ("%s -X -v ON_ERROR_STOP=1 -A -t -h 127.0.0.1 -p %s "
                     "-U postgres -d %s -c %r" %
                     (PSQL, PORT135, database, "SELECT count(*) FROM t31"))
    wait = ("for i in $(seq 1 30); do v=$(%s); test \"$v\" = 1 && exit 0; "
            "sleep 1; done; exit 1" % compact_query)
    return [
        visible("在 node134 的 %s 创建 t31 并订阅" % database,
                psql(PORT134, database, create), "创建表并异步刷新成功",
                timeout=180, statement=create, report_node="node134"),
        visible("在 node134 的 %s 写入 t31 业务数据" % database,
                psql(PORT134, database, insert), "返回 INSERT 0 1",
                statement=insert, report_node="node134"),
        visible("等待 node135 的 %s 收到 t31 业务数据" % database, wait,
                "30 秒内查询 count(*) 返回 1", timeout=35,
                statement="SELECT count(*) FROM t31;", report_node="node135"),
    ]


CASE = {
    "id": "mmr.node_management.multi_database_active_join",
    "name": "[LONG-TIME] 四数据库双节点 all join、集群校验与业务复制",
    "document": "多活功能测试文档.md", "section": "2.3.2.2 测试一", "group": "node_management",
    "default_enabled": False,
    "fixtures": ["cluster", {"type": "isolated_mmr_node_creation", "data_dir": ROOT}],
    "requirements": {"clusters": ["mmr"], "plugins": ["fdd_mmr"],
                     "writable_node": True, "node": "mmr:mmr1"},
    "evidence_nodes": ["mmr:mmr1"],
    "test_topology": {
        "summary": "节点数=2；两个对等 MMR 节点各运行 postgres、test 和两个长库名数据库。",
        "nodes": [
            {"name": "node134", "role": "MMR primary", "host": "127.0.0.1", "port": PORT134, "data_dir": NODE134},
            {"name": "node135", "role": "MMR primary", "host": "127.0.0.1", "port": PORT135, "data_dir": NODE135},
        ],
        "relations": ["MMR 多活: node134 <-> node135；四个数据库各有独立多活组。", "物理流复制: 无"],
    },
    "prerequisites": [
        "按文档 fdd.running_databases 同时配置四个数据库；临时节点具备 logical WAL、fdd_mmr 预加载和 license。",
        "使用文档 create.sql、insert_data.sql；因 create.sql 没有切换到新库，表定义主体在另外三个库复用执行。",
    ],
    "steps": [
        init_instance(NODE134, PORT134), init_instance(NODE135, PORT135),
        create_document_databases(PORT134), create_document_databases(PORT135),
        seed_document_data(PORT134), create_groups(), create_joiner_nodes(), *join_groups(),
        *[check_cluster(PORT134, "node134", database) for database in DATABASES],
        *[check_cluster(PORT135, "node135", database) for database in DATABASES],
        *[step for database in DATABASES for step in t31_flow(database)],
        *[check_cluster(PORT134, "node134", database) for database in DATABASES],
        *[check_cluster(PORT135, "node135", database) for database in DATABASES],
    ],
    "teardown": "以 immediate 停止 node134/node135 临时实例并删除临时目录，移除四个数据库、31 表、订阅、复制槽和 MMR 元数据。",
}
