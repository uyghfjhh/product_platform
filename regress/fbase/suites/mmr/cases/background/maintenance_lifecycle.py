from framework.assertions import command_succeeds, output_contains
from framework.steps import command_step


DATA = "/tmp/fbase_regress_mmr_daemon_{run_id}"
PORT = "15445"
PGHOME = "/usr/local/fbase15.15"
PGCTL = PGHOME + "/bin/pg_ctl"
PSQL = PGHOME + "/bin/psql"
NODE = "daemon_node"


def shell(title, script, expected, assertion=command_succeeds(), timeout=30):
    return command_step(title, ["sh", "-ec", script], expected, assertion,
                        node="mmr:mmr1", timeout=timeout)


def activity_check(title, expected):
    sql = (
        "SELECT count(*) FILTER (WHERE backend_type='fdd mmr supervisor')::text || '|' || "
        "count(*) FILTER (WHERE backend_type LIKE 'fdd mmr maintenance daemon:%')::text "
        "FROM pg_stat_activity")
    return shell(title, "%s -X -At -h 127.0.0.1 -p %s -U postgres -d postgres -c %r" %
                 (PSQL, PORT, sql), "输出 %s（supervisor|maintenance daemon）" % expected,
                 output_contains(expected))


CASE = {
    "id": "mmr.background.maintenance_lifecycle",
    "name": "多活 supervisor 与后台维护进程生命周期",
    "document": "多活功能测试文档.md",
    "section": "6",
    "group": "background",
    "fixtures": ["cluster", {"type": "isolated_mmr_daemon", "data_dir": DATA,
                               "port": PORT}],
    "test_topology": {
        "summary": "节点数=1；单实例后台进程生命周期验证；无复制关系。",
        "nodes": [{"name": NODE, "role": "standalone PostgreSQL", "host": "127.0.0.1",
                   "port": PORT, "data_dir": DATA}],
        "relations": ["物理流复制: 无", "逻辑复制: 无", "MMR 多活: 尚未建组"],
    },
    "requirements": {
        "clusters": ["mmr"], "plugins": ["fdd_mmr"],
        "writable_node": True, "node": "mmr:mmr1",
    },
    "evidence_nodes": ["mmr:mmr1"],
    "prerequisites": [
        "隔离实例使用 fdd_mmr 预加载、wal_level=logical 和本机有效 license。",
        "实例数据目录位于 /tmp/fbase_regress_ 下，fixture 在任意退出路径立即停止并删除它。",
    ],
    "steps": [
        shell("初始化未创建扩展的隔离 PostgreSQL 实例",
              "rm -rf %s; %s/bin/initdb -D %s -U postgres --auth-local=trust --auth-host=trust" %
              (DATA, PGHOME, DATA), "返回 Success"),
        shell("配置 fdd_mmr 预加载和后台进程所需参数",
              "cp /home/postgres/license/license.dat %s/license.dat; "
              "printf \"\\nshared_preload_libraries = 'fdd_mmr'\\nfdd.running_databases = 'postgres'\\n"
              "wal_level = logical\\nmax_worker_processes = 8\\nmax_logical_replication_workers = 4\\n"
              "max_replication_slots = 10\\nmax_wal_senders = 10\\ntrack_commit_timestamp = on\\n"
              "listen_addresses = '127.0.0.1'\\nport = %s\\nlogging_collector = on\\nlog_directory = 'log'\\n\" "
              ">> %s/postgresql.conf" % (DATA, PORT, DATA), "返回 0，配置写入成功"),
        shell("启动未创建扩展的隔离实例",
              "%s -D %s -l %s/start.log -w start" % (PGCTL, DATA, DATA),
              "返回 server started"),
        activity_check("确认未创建扩展时仅 supervisor 运行", "1|0"),
        shell("创建 fdd_mmr 扩展但尚不调用多活 UDF",
              "%s -X -v ON_ERROR_STOP=1 -h 127.0.0.1 -p %s -U postgres -d postgres "
              "-c 'CREATE EXTENSION fdd_mmr'" % (PSQL, PORT), "返回 CREATE EXTENSION"),
        activity_check("确认创建扩展后 maintenance daemon 尚未立即启动", "1|0"),
        shell("调用 create_node 触发多活后台维护进程",
              "%s -X -v ON_ERROR_STOP=1 -h 127.0.0.1 -p %s -U postgres -d postgres "
              "-c %r" % (PSQL, PORT, "SELECT fdd.create_node('%s', 'host=127.0.0.1 port=%s user=postgres dbname=postgres')" % (NODE, PORT)),
              "返回 local node create successful"),
        shell("等待 maintenance daemon 注册", "sleep 2", "返回 0"),
        activity_check("确认 create_node 后 supervisor 和 maintenance daemon 均运行", "1|1"),
        shell("按文档分离唯一节点", "%s -X -v ON_ERROR_STOP=1 -h 127.0.0.1 -p %s -U postgres -d postgres -c %r" %
              (PSQL, PORT, "SELECT fdd.part_node('%s')" % NODE), "返回 part node successful"),
        shell("等待分离后的 maintenance daemon 退出", "sleep 2", "返回 0"),
        activity_check("确认节点分离后仅 supervisor 保留", "1|0"),
    ],
    "teardown": "isolated_mmr_daemon fixture 以 immediate 停止隔离实例并删除全部数据文件。",
}
