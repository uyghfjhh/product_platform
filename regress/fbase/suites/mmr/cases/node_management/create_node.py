from framework.assertions import command_succeeds, output_contains
from framework.steps import command_step


ROOT = "/tmp/fbase_regress_mmr_node_creation_{run_id}"
DEFAULT_DATA = ROOT + "/default"
LONG_DATA = ROOT + "/long_name"
PGHOME = "/usr/local/fbase15.15"
PGCTL = PGHOME + "/bin/pg_ctl"
PSQL = PGHOME + "/bin/psql"
DEFAULT_PORT = "15450"
LONG_PORT = "15451"
LONG_NAME = "1234567890123456789012345678901234567890123456789012345678901234567890"
TRUNCATED_NAME = LONG_NAME[:63]


def shell(title, script, expected, assertion=command_succeeds(), timeout=30):
    return command_step(title, ["sh", "-ec", script], expected, assertion,
                        node="mmr:mmr1", timeout=timeout)


def psql(port, sql):
    return "%s -X -v ON_ERROR_STOP=1 -h 127.0.0.1 -p %s -U postgres -d postgres -c %r" % (
        PSQL, port, sql)


def init_instance(title, data_dir, port):
    step = shell(title,
                 "mkdir -p %s; %s/bin/initdb -D %s -U postgres --auth-local=trust --auth-host=trust; "
                 "cp /home/postgres/license/license.dat %s/license.dat; "
                 "printf \"\\nshared_preload_libraries = 'fdd_mmr'\\nfdd.running_databases = 'postgres'\\n"
                 "wal_level = logical\\ntrack_commit_timestamp = on\\nmax_worker_processes = 8\\n"
                 "max_logical_replication_workers = 4\\nmax_replication_slots = 10\\nmax_wal_senders = 10\\n"
                 "listen_addresses = '127.0.0.1'\\nport = %s\\n\" >> %s/postgresql.conf; "
                 "%s -D %s -l %s/start.log -w start; "
                 "%s; %s" % (data_dir, PGHOME, data_dir, data_dir, port, data_dir,
                               PGCTL, data_dir, data_dir,
                               psql(port, "CREATE EXTENSION fdd_mmr"),
                               psql(port, "CREATE EXTENSION fb_license")),
                 "初始化、启动并创建两个扩展成功", timeout=40)
    step["report"] = False
    return step


CASE = {
    "id": "mmr.node_management.create_node",
    "name": "多活节点创建默认参数与名称截断",
    "document": "多活功能测试文档.md", "section": "2.1", "group": "node_management",
    "fixtures": ["cluster", {"type": "isolated_mmr_node_creation", "data_dir": ROOT}],
    "requirements": {"clusters": ["mmr"], "plugins": ["fdd_mmr"],
                     "writable_node": True, "node": "mmr:mmr1"},
    "evidence_nodes": ["mmr:mmr1"],
    "prerequisites": [
        "两个临时实例均使用 fdd_mmr 预加载、logical WAL 和本机 license；每个实例在首次 create_node 前没有本地节点元数据。",
        "为完整覆盖文档两个首次创建场景，默认节点与长名称节点分别使用独立实例。",
    ],
    "steps": [
        init_instance("初始化默认参数节点隔离实例", DEFAULT_DATA, DEFAULT_PORT),
        shell("按文档以默认 failover、streaming、two_phase 创建节点",
              psql(DEFAULT_PORT, "SELECT fdd.create_node('node134', 'host=127.0.0.1 port=15450 user=postgres dbname=postgres')"),
              "返回 local node create successful"),
        shell("确认默认创建后的本地节点元数据", "%s -X -At -F '|' -h 127.0.0.1 -p %s -U postgres -d postgres -c %r" %
              (PSQL, DEFAULT_PORT, "SELECT node_id::text,set_mode::text,join_wait_mode::text,precheck::text FROM fdd.mmr_local_node"),
              "输出 -1|d|i|table_exist_error", output_contains("-1|d|i|table_exist_error")),
        shell("确认默认创建的节点属性", "%s -X -At -F '|' -h 127.0.0.1 -p %s -U postgres -d postgres -c %r" %
              (PSQL, DEFAULT_PORT, "SELECT node_id::text,node_name::text,node_state::text,failover::text,streaming::text,two_phase::text FROM fdd.mmr_node"),
              "输出 -1|node134|CREATED|true|f|false", output_contains("-1|node134|CREATED|true|f|false")),
        init_instance("初始化长节点名隔离实例", LONG_DATA, LONG_PORT),
        shell("按文档以超过 63 字符的名称创建节点",
              psql(LONG_PORT, "SELECT fdd.create_node('%s', 'host=127.0.0.1 port=15451 user=postgres dbname=postgres')" % LONG_NAME),
              "返回 local node create successful"),
        shell("确认节点名被截断为前 63 个字符且保留默认属性", "%s -X -At -F '|' -h 127.0.0.1 -p %s -U postgres -d postgres -c %r" %
              (PSQL, LONG_PORT, "SELECT node_name::text,length(node_name)::text,failover::text,streaming::text,two_phase::text FROM fdd.mmr_node"),
              "输出 %s|63|true|f|false" % TRUNCATED_NAME,
              output_contains("%s|63|true|f|false" % TRUNCATED_NAME)),
    ],
    "teardown": "fixture 以 immediate 停止两个隔离实例并删除整个临时目录。",
}
