"""Document 2.3.2.2 test two: four databases on three MMR peers."""

from framework.assertions import command_fails, command_succeeds, output_contains_text
from framework.steps import command_step
from suites.mmr.cases.conflict.delete_missing import setup
from suites.mmr.cases.node_management.create_group import PGCTL, PGHOME, PSQL, shell

ROOT = "/tmp/fbase_regress_mmr_multi_database_three_{run_id}"
A, B, C = ROOT + "/node134", ROOT + "/node135", ROOT + "/node136"
PA, PB, PC = "15581", "15582", "15583"
DOC_ROOT = "/home/postgres/fly_dev/postgresql_for_fbase_dev/contrib/fdd_mmr/doc/转测文档/必测用例/多数据库实例测试用例"
CREATE, INSERT = DOC_ROOT + "/create.sql", DOC_ROOT + "/insert_data.sql"
DBS = ("postgres", "test", "abcdefghijklmnopqrstuvwxyz_1234567890_1234567890_1234567890_db1", "abcdefghijklmnopqrstuvwxyz_1234567890_1234567890_1234567890_db2")
GROUPS = ("group_postgres", "group_test", "group_abcdefghijklmnopqrstuvwxyz_0123456789_01234567890_dbname1", "group_abcdefghijklmnopqrstuvwxyz_0123456789_01234567890_dbname2")
NA, NB, NC = ("node1", "node1", "node1", "nodea"), ("node2", "node2", "node2", "nodeb"), ("node3", "node3", "node3", "nodec")

def psql(port, db, statement):
    return "%s -X -v ON_ERROR_STOP=1 -P pager=off -h 127.0.0.1 -p %s -U postgres -d %s -c %r" % (PSQL, port, db, statement)

def command(title, port, db, statement, expected, assertion=command_succeeds(), timeout=90, node=None):
    step = command_step(title, ["sh", "-ec", psql(port, db, statement)], expected, assertion, node="mmr:mmr1", timeout=timeout, display_sql=statement, report_node=node)
    step["report_database"] = db
    return step

def scalar_wait(title, port, db, statement, expected, node):
    return multi_scalar_wait(title, [(node, port, db, statement, expected)])


def multi_scalar_wait(title, checks):
    """Poll independent databases together so one slow group cannot multiply wait time."""
    probes = []
    display_sql = []
    for node, port, db, statement, expected in checks:
        probes.append(
            "v=$(%s -X -At -h 127.0.0.1 -p %s -U postgres -d %s -c %r); "
            "values=\"${values}%s | %s | %s | $v\\n\"; test \"$v\" = %r || ok=false" %
            (PSQL, port, db, statement, node, db, expected, expected))
        display_sql.append("-- %s / %s\n%s;" % (node, db, statement))
    script = (
        "for i in $(seq 1 60); do ok=true; values=''; %s; "
        "if $ok; then printf '%%b' \"$values\"; exit 0; fi; sleep 1; done; "
        "printf '%%b' \"$values\"; exit 1" % "; ".join(probes))
    step = command_step(
        title, ["sh", "-ec", script],
        "60 秒内所有数据库均满足预期（输出为 节点 | 数据库 | 预期值 | 实际值）",
        command_succeeds(), node="mmr:mmr1", timeout=65,
        display_sql="\n".join(display_sql), report_node="node134")
    return step

def query(title, port, db, statement, expected, *markers, **opts):
    return command(title, port, db, statement, expected, output_contains_text(*(markers or (expected,))), node=opts.get("node"))

def init(data, port):
    script = ("mkdir -p {d}; {h}/bin/initdb -D {d} -U postgres --auth-local=trust --auth-host=trust; cp /home/postgres/license/license.dat {d}/license.dat; printf \"\\nshared_preload_libraries = 'fdd_mmr'\\nfdd.running_databases = '{dbs}'\\nwal_level = logical\\ntrack_commit_timestamp = on\\nfdd.part_catchup_timeout = '60s'\\nmax_worker_processes = 30\\nmax_logical_replication_workers = 24\\nmax_sync_workers_per_subscription = 2\\nmax_replication_slots = 24\\nmax_wal_senders = 24\\nlisten_addresses = '127.0.0.1'\\nport = {p}\\n\" >> {d}/postgresql.conf; {h}/bin/pg_ctl -D {d} -l {d}/start.log -w start").format(d=data, h=PGHOME, dbs=",".join(DBS), p=port)
    return setup(shell("初始化四数据库临时实例", script, "启动成功", timeout=60))

def base_databases(port, tables, seed=False):
    actions = ["%s -f %s" % (psql_base(port, "postgres"), CREATE)]
    for db in DBS[1:]:
        actions.append("tail -n +5 %s | %s" % (CREATE, psql_base(port, db)))
    for db in DBS:
        actions.append(psql(port, db, "CREATE EXTENSION fdd_mmr; CREATE EXTENSION fb_license"))
    if seed:
        actions.extend("%s -f %s" % (psql_base(port, db), INSERT) for db in DBS)
    return setup(shell("按文档创建四个数据库、表、扩展%s" % ("和存量数据" if seed else ""), "; ".join(actions), "完成四数据库文档准备", timeout=240))

def empty_databases(port):
    actions = []
    for db in DBS[1:]:
        actions.append(psql(port, "postgres", "CREATE DATABASE %s" % db))
    for db in DBS:
        actions.append(psql(port, db, "CREATE EXTENSION fdd_mmr; CREATE EXTENSION fb_license"))
    return setup(shell("按文档创建四个空数据库并安装扩展", "; ".join(actions), "完成空数据库准备", timeout=120))

def psql_base(port, db):
    return "%s -X -v ON_ERROR_STOP=1 -h 127.0.0.1 -p %s -U postgres -d %s" % (PSQL, port, db)

def create_nodes(port, names, groups=False):
    actions = []
    for db, name, group in zip(DBS, names, GROUPS):
        dsn = "host=127.0.0.1 port=%s user=postgres dbname=%s" % (port, db)
        statement = "SELECT fdd.create_node('%s','%s')" % (name, dsn)
        if groups:
            statement += "; SELECT fdd.create_group('%s')" % group
        actions.append(psql(port, db, statement))
    return setup(shell("在四个数据库创建本地节点%s" % ("和组" if groups else ""), "; ".join(actions), "创建成功", timeout=180))

def joins(port, node, mappings, document_cross_dsn=False):
    steps = []
    for local_db, group, remote_db in mappings:
        dsn = "host=127.0.0.1 port=%s user=postgres dbname=%s" % (PA, remote_db)
        statement = "SELECT fdd.join_group('%s','%s',true,'all','table_exist_error')" % (group, dsn)
        is_cross_dsn = document_cross_dsn and local_db in DBS[2:]
        step = command(
            "按原文将 %s 的 %s 加入 %s" % (node, local_db, group), port, local_db,
            statement,
            "返回 group name not same as join target" if is_cross_dsn else "返回 node join to group complete finished",
            command_fails("group name not same as join target") if is_cross_dsn else command_succeeds(),
            timeout=180, node=node)
        steps.append(step)
    return steps

def health(port, node, db, count):
    sql = "SELECT count(*)::text || '|' || bool_and(is_abnormal='OK')::text FROM fdd.show_node_info(true,false)"
    return (node, port, db, sql, "%s|true" % count)

def t31(db):
    create = "SELECT fdd.run_on_all_nodes('CREATE TABLE t31(id int PRIMARY KEY,name1 text,name2 text)'); SELECT fdd.replication_set_async_execute(true)"
    return [command("在 node134 的 %s 创建并订阅 t31" % db, PA, db, create, "创建和异步刷新成功", timeout=180, node="node134"), command("在 node134 的 %s 写入 t31" % db, PA, db, "INSERT INTO t31 VALUES(1,'a1','a2')", "返回 INSERT 0 1", node="node134")]

def part_steps():
    steps = []
    for db in DBS:
        wrong_node = db == DBS[-1]
        step = command("按文档在 node134 的 %s 分离 node2" % db, PA, db,
                       "SELECT fdd.part_node('node2')",
                       "返回 node2 不属于该组" if wrong_node else "分离完成",
                       command_fails("node2") if wrong_node else command_succeeds(),
                       timeout=120, node="node134")
        steps.append(step)
    steps.append(command("按实际长库名节点在 node134 的 %s 分离 nodeb" % DBS[-1], PA,
                         DBS[-1], "SELECT fdd.part_node('nodeb')", "分离完成",
                         timeout=120, node="node134"))
    return steps


def drop_steps():
    steps = []
    for db in DBS:
        wrong_node = db == DBS[-1]
        steps.append(command(
            "按文档在 node134 的 %s 删除 node2" % db, PA, db,
            "SELECT fdd.drop_node('node2')",
            "返回 node2 不属于该组" if wrong_node else "删除完成",
            command_fails("node2") if wrong_node else command_succeeds(),
            timeout=120, node="node134"))
    steps.append(command("按实际长库名节点在 node134 的 %s 删除 nodeb" % DBS[-1], PA,
                         DBS[-1], "SELECT fdd.drop_node('nodeb')", "删除完成",
                         timeout=120, node="node134"))
    return steps

ABC = tuple((db, group, db) for db, group in zip(DBS, GROUPS))
# The source document explicitly cross-connects the two long database names.
DOC_C = ( (DBS[0], GROUPS[0], DBS[0]), (DBS[1], GROUPS[1], DBS[1]), (DBS[2], GROUPS[2], DBS[3]), (DBS[3], GROUPS[3], DBS[2]) )
FIXED_C = ((DBS[2], GROUPS[2], DBS[2]), (DBS[3], GROUPS[3], DBS[3]))

CASE = {
 "id":"mmr.node_management.multi_database_three_node_join", "name":"[LONG-TIME] 四数据库三节点 join、分离和删除后的复制", "document":"多活功能测试文档.md", "section":"2.3.2.2 测试二", "group":"node_management", "known_issue":"D-046", "default_enabled":False,
 "fixtures":["cluster", {"type":"isolated_mmr_node_creation","data_dir":ROOT}], "requirements":{"clusters":["mmr"],"plugins":["fdd_mmr"],"writable_node":True,"node":"mmr:mmr1"}, "evidence_nodes":["mmr:mmr1"],
 "test_topology":{"summary":"节点数=3；node134/node135/node136 各运行四个数据库；每个数据库独立 MMR 组。","nodes":[{"name":"node134","role":"MMR primary","host":"127.0.0.1","port":PA,"data_dir":A},{"name":"node135","role":"MMR primary（后续分离/删除）","host":"127.0.0.1","port":PB,"data_dir":B},{"name":"node136","role":"MMR primary","host":"127.0.0.1","port":PC,"data_dir":C}],"relations":["MMR 多活: 每个数据库 node134 <-> node135 -> node136","物理流复制: 无"]},
 "prerequisites":["复用文档 create.sql、insert_data.sql 的四库 31 表与存量。C 原文长库名交叉 DSN、db2 使用 node2 的两处 SQL 均作为文档错误证据保留；随后按同库 DSN 和 nodeb 执行正确步骤。"],
 "steps":[init(A,PA),base_databases(PA,True,True),create_nodes(PA,NA,True),init(B,PB),empty_databases(PB),create_nodes(PB,NB),*joins(PB,"node135",ABC),init(C,PC),empty_databases(PC),create_nodes(PC,NC),*joins(PC,"node136",DOC_C,True), *joins(PC,"node136",FIXED_C), multi_scalar_wait("等待三节点四数据库集群校验正常", [health(port,node,db,3) for port,node in ((PA,"node134"),(PB,"node135"),(PC,"node136")) for db in DBS]), *[x for db in DBS for x in t31(db)], multi_scalar_wait("等待 node136 的四个数据库均收到 t31", [("node136",PC,db,"SELECT count(*) FROM t31","1") for db in DBS]), *part_steps(), multi_scalar_wait("等待分离 node135 后 node134 四个数据库仍健康", [health(PA,"node134",db,3) for db in DBS]), *drop_steps(), multi_scalar_wait("等待删除 node135 后 node134 四个数据库恢复两成员健康", [health(PA,"node134",db,2) for db in DBS]), *[command("删除 node135 后在 node134 的 %s 写入业务数据"%db,PA,db,"INSERT INTO t31 VALUES(2,'a2','a2')","返回 INSERT 0 1",node="node134") for db in DBS], multi_scalar_wait("等待 node136 的四个数据库均收到删除后的业务数据", [("node136",PC,db,"SELECT count(*) FROM t31","2") for db in DBS])],
 "teardown":"以 immediate 停止 node134/node135/node136 并删除临时目录，移除四库表、订阅、复制槽、分离和删除元数据。",
}
