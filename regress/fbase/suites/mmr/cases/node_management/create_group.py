from framework.assertions import command_fails, command_succeeds, output_contains
from framework.steps import command_step


ROOT = "/tmp/fbase_regress_mmr_group_creation_{run_id}"
DEFAULT_DATA = ROOT + "/default"
LONG_DATA = ROOT + "/long_name"
PGHOME = "/usr/local/fbase15.15"
PGCTL = PGHOME + "/bin/pg_ctl"
PSQL = PGHOME + "/bin/psql"
DEFAULT_PORT = "15452"
LONG_PORT = "15453"
LONG_NAME = "group0123456789012345678901234567890123456789012345678901234567890123456789"
TRUNCATED_NAME = LONG_NAME[:63]


def shell(title, script, expected, assertion=command_succeeds(), timeout=30):
    return command_step(title, ["sh", "-ec", script], expected, assertion,
                        node="mmr:mmr1", timeout=timeout)


def psql(port, sql):
    return "%s -X -v ON_ERROR_STOP=1 -h 127.0.0.1 -p %s -U postgres -d postgres -c %r" % (
        PSQL, port, sql)


def init_instance(title, data_dir, port, extra_settings=""):
    step = shell(title,
                 "mkdir -p %s; %s/bin/initdb -D %s -U postgres --auth-local=trust --auth-host=trust; "
                 "cp /home/postgres/license/license.dat %s/license.dat; "
                 "printf \"\\nshared_preload_libraries = 'fdd_mmr'\\nfdd.running_databases = 'postgres'\\n"
                 "wal_level = logical\\ntrack_commit_timestamp = on\\nmax_worker_processes = 16\\n"
                 "max_logical_replication_workers = 8\\nmax_parallel_apply_workers_per_subscription = 2\\n"
                 "max_sync_workers_per_subscription = 2\\nmax_replication_slots = 10\\nmax_wal_senders = 12\\n"
                 "debug_logical_replication_streaming = 'buffered'\\nlogical_decoding_work_mem = '64MB'\\n"
                 "listen_addresses = '127.0.0.1'\\nport = %s\\n%s\" >> %s/postgresql.conf; "
                 "for attempt in $(seq 1 20); do "
                 "%s -D %s -l %s/start.log -w start && break; "
                 "if grep -q 'Address already in use' %s/start.log && test \"$attempt\" -lt 20; then "
                 "sleep 0.5; else exit 1; fi; "
                 "done; %s; %s" %
                 (data_dir, PGHOME, data_dir, data_dir, port, extra_settings, data_dir, PGCTL, data_dir,
                  data_dir, data_dir, psql(port, "CREATE EXTENSION fdd_mmr"),
                  psql(port, "CREATE EXTENSION fb_license")),
                 "初始化、启动并创建两个扩展成功", timeout=40)
    step["report"] = False
    return step


def create_node(port, name, streaming="off", two_phase=False):
    step = shell("在隔离实例创建本地节点 %s" % name,
                 psql(port, "SELECT fdd.create_node('%s', 'host=127.0.0.1 port=%s user=postgres dbname=postgres',true,'%s',%s)" %
                      (name, port, streaming, "true" if two_phase else "false")),
                 "返回 local node create successful")
    step["report"] = False
    return step


CASE = {
    "id": "mmr.node_management.create_group",
    "name": "多活集群创建、重复拒绝与名称截断",
    "document": "多活功能测试文档.md", "section": "2.2 测试一,2.2 测试二,2.2 测试三",
    "group": "node_management",
    "fixtures": ["cluster", {"type": "isolated_mmr_node_creation", "data_dir": ROOT}],
    "requirements": {"clusters": ["mmr"], "plugins": ["fdd_mmr"],
                     "writable_node": True, "node": "mmr:mmr1"},
    "evidence_nodes": ["mmr:mmr1"],
    "prerequisites": [
        "两个临时实例均没有本地节点和组；分别用于普通组名和超过 63 字符组名的首次创建。",
        "实例使用 loopback DSN，仅隔离验证文档 UDF 及元数据，不影响已有多活集群。",
    ],
    "steps": [
        init_instance("初始化普通组名隔离实例", DEFAULT_DATA, DEFAULT_PORT),
        create_node(DEFAULT_PORT, "node134"),
        shell("按文档创建 group 集群", psql(DEFAULT_PORT, "SELECT fdd.create_group('group')"),
              "返回 node group create successful"),
        shell("确认创建的集群元数据和默认复制集", "%s -X -At -F '|' -h 127.0.0.1 -p %s -U postgres -d postgres -c %r" %
              (PSQL, DEFAULT_PORT, "SELECT group_id::text,group_name::text,group_default_repsets::text,group_parent_id::text,(assigned_node_id > 0)::text FROM fdd.mmr_group"),
              "输出 1|group|{group}|1|true", output_contains("1|group|{group}|1|true")),
        shell("确认创建组的本地节点自动成为 ACTIVE 成员", "%s -X -At -F '|' -h 127.0.0.1 -p %s -U postgres -d postgres -c %r" %
              (PSQL, DEFAULT_PORT, "SELECT node_id::text,node_name::text,group_id::text,source_node_id::text,node_state::text FROM fdd.mmr_node"),
              "输出 1|node134|1|1|ACTIVE", output_contains("1|node134|1|1|ACTIVE")),
        shell("按文档再次创建集群并确认被拒绝", psql(DEFAULT_PORT, "SELECT fdd.create_group('group1')"),
              "返回错误 local node alread in node group", command_fails("local node alread in node group")),
        init_instance("初始化长组名隔离实例", LONG_DATA, LONG_PORT),
        create_node(LONG_PORT, "node135"),
        shell("按文档以超过 63 字符的名称创建集群", psql(LONG_PORT, "SELECT fdd.create_group('%s')" % LONG_NAME),
              "返回 node group create successful"),
        shell("确认组名和默认复制集被截断为前 63 个字符", "%s -X -At -F '|' -h 127.0.0.1 -p %s -U postgres -d postgres -c %r" %
              (PSQL, LONG_PORT, "SELECT group_name::text,length(group_name)::text,group_default_repsets::text FROM fdd.mmr_group"),
              "输出 %s|63|{%s}" % (TRUNCATED_NAME, TRUNCATED_NAME),
              output_contains("%s|63|{%s}" % (TRUNCATED_NAME, TRUNCATED_NAME))),
    ],
    "teardown": "fixture 以 immediate 停止两个隔离实例并删除整个临时目录。",
}
