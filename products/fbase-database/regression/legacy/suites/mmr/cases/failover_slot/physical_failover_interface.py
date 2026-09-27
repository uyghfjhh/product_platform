from suites.mmr.cases.conflict.delete_missing import query, setup, sql
from suites.mmr.cases.node_management.create_group import (PGCTL, PSQL, create_node,
                                                            init_instance, psql, shell)


ROOT = "/tmp/fbase_regress_mmr_physical_failover_{run_id}"
PRIMARY, STANDBY, PEER = ROOT + "/primary", ROOT + "/standby", ROOT + "/peer"
PRIMARY_PORT, STANDBY_PORT, PEER_PORT = "15521", "15522", "15523"
PRIMARY_DSN = "host=127.0.0.1 port=%s user=postgres dbname=postgres" % PRIMARY_PORT
STANDBY_DSN = "host=127.0.0.1 port=%s user=postgres dbname=postgres" % STANDBY_PORT
TABLE = "physical_failover_{run_id}"


def wait_for(title, port, statement, expected, timeout=40):
    return sql(title, "for i in $(seq 1 35); do v=$(%s -X -At -h 127.0.0.1 -p %s -U postgres -d postgres -c %r); test \"$v\" = %r && { echo \"$v\"; exit 0; }; sleep 1; done; exit 1" % (PSQL, port, statement, expected), "35 秒内输出 %s" % expected, statement, timeout)


CASE = {
    "id": "mmr.failover_slot.physical_failover_interface", "name": "物理主备提升后的故障转移槽与节点 DSN 更新",
    "document": "多活功能测试文档.md", "section": "7.2,7.3", "group": "failover_slot",
    "fixtures": ["cluster", {"type": "isolated_mmr_node_creation", "data_dir": ROOT}],
    "test_topology": {
        "summary": "节点数=3；node134 主库与其物理备库，加 node135 对等 MMR 成员。",
        "nodes": [
            {"name": "node134", "role": "MMR primary（切换前）", "host": "127.0.0.1",
             "port": PRIMARY_PORT, "data_dir": PRIMARY},
            {"name": "node134_standby", "role": "node134 物理备库（切换后提升）",
             "host": "127.0.0.1", "port": STANDBY_PORT, "data_dir": STANDBY},
            {"name": "node135", "role": "MMR primary", "host": "127.0.0.1",
             "port": PEER_PORT, "data_dir": PEER},
        ],
        "relations": ["物理流复制: node134 -> node134_standby", "MMR 多活: node134 <-> node135"],
    },
    "requirements": {"clusters": ["mmr"], "plugins": ["fdd_mmr"], "writable_node": True, "node": "mmr:mmr1"}, "evidence_nodes": ["mmr:mmr1"],
    "prerequisites": ["在临时目录建立 node134 主库、其物理备库和 node135 多活节点；node134/node135 均使用 failover=true。", "物理备库由 pg_basebackup 创建并在切换前持续回放；切换后 node135 与新主均用 alter_node_interface 更新 node134 的 DSN。"],
    "steps": [
        setup(init_instance("初始化 node134 旧主库", PRIMARY, PRIMARY_PORT)), setup(create_node(PRIMARY_PORT, "node134")), setup(shell("创建切换验证表和初始数据", psql(PRIMARY_PORT, "CREATE TABLE public.%s(id int PRIMARY KEY,name text); INSERT INTO public.%s VALUES(1,'1'),(3,'3')" % (TABLE,TABLE)), "返回 CREATE TABLE 和 INSERT 0 2")), setup(shell("创建 g1 集群", psql(PRIMARY_PORT, "SELECT fdd.create_group('g1')"), "返回 node group create successful")),
        setup(init_instance("初始化 node135 多活节点", PEER, PEER_PORT)), setup(create_node(PEER_PORT, "node135")), setup(shell("以 all 模式将 node135 加入 g1", psql(PEER_PORT, "SELECT fdd.join_group('g1','%s',true,'all','table_exist_error')" % PRIMARY_DSN), "返回 node join to group complete finished", timeout=90)),
        setup(shell("使用 pg_basebackup 创建 node134 的物理备库并启动", "mkdir -p %s; chmod 700 %s; %s -h 127.0.0.1 -p %s -U postgres -D %s -R -X stream -c fast; cp %s/license.dat %s/license.dat; printf \"\\nport = %s\\nlisten_addresses = '127.0.0.1'\\n\" >> %s/postgresql.conf; %s -D %s -l %s/start.log -w start || { cat %s/start.log; exit 1; }" % (STANDBY, STANDBY, "/usr/local/fbase15.15/bin/pg_basebackup", PRIMARY_PORT, STANDBY, PRIMARY, STANDBY, STANDBY_PORT, STANDBY, PGCTL, STANDBY, STANDBY, STANDBY), "物理备库完成 basebackup 并启动", timeout=90)),
        query("读取 node134 旧主切换前角色", PRIMARY_PORT, "SELECT pg_is_in_recovery() AS is_standby", "返回 false", "f"), query("读取 node134 物理备库切换前角色", STANDBY_PORT, "SELECT pg_is_in_recovery() AS is_standby", "返回 true", "t"),
        query("展示 node134/node135 的切换前 DSN 元数据", PEER_PORT, "SELECT node_name,node_dsn,failover FROM fdd.mmr_node WHERE node_name IN ('node134','node135') ORDER BY node_name", "node134 指向旧主端口且两个节点 failover=true", "node134", str(PRIMARY_PORT), "node135", "t"),
        query("展示旧主 node134 的 fddoutput 故障转移槽", PRIMARY_PORT, "SELECT count(*)::text AS slot_count,bool_and(failover)::text AS all_failover FROM pg_get_replication_slots() WHERE plugin='fddoutput'", "至少一个 fddoutput 槽且全部 failover=true", "1", "t"),
        query("展示 node135 接收的初始业务数据", PEER_PORT, "SELECT id,name FROM public.%s ORDER BY id" % TABLE, "返回 id=1,3", "1", "3", "(2 rows)"),
        setup(shell("停止 node134 旧主库以模拟主库宕机", "%s -D %s stop -m immediate" % (PGCTL, PRIMARY), "旧主停止", timeout=40)), setup(shell("提升 node134 物理备库为新主", "%s -D %s promote -w" % (PGCTL, STANDBY), "物理备库提升成功", timeout=40)),
        query("读取 node134 新主提升后角色", STANDBY_PORT, "SELECT pg_is_in_recovery() AS is_standby", "返回 false", "f"), query("展示提升后新主的 fddoutput 故障转移槽", STANDBY_PORT, "SELECT count(*)::text AS slot_count,bool_and(failover)::text AS all_failover FROM pg_get_replication_slots() WHERE plugin='fddoutput'", "至少一个 fddoutput 槽且全部 failover=true", "1", "t"),
        sql("按文档在 node135 将 node134 DSN 改为新主", psql(PEER_PORT, "SELECT fdd.alter_node_interface('node134','%s',false)" % STANDBY_DSN), "返回 true", "SELECT fdd.alter_node_interface('node134','%s',false)" % STANDBY_DSN),
        sql("按文档在提升后的新主将自身 node134 DSN 改为新主", psql(STANDBY_PORT, "SELECT fdd.alter_node_interface('node134','%s',false)" % STANDBY_DSN), "返回 true", "SELECT fdd.alter_node_interface('node134','%s',false)" % STANDBY_DSN),
        query("展示 node135 上更新后的 node134 DSN", PEER_PORT, "SELECT node_name,node_dsn FROM fdd.mmr_node WHERE node_name='node134'", "返回 node134 和新主端口", "node134", STANDBY_PORT), query("展示新主上更新后的 node134 DSN", STANDBY_PORT, "SELECT node_name,node_dsn FROM fdd.mmr_node WHERE node_name='node134'", "返回 node134 和新主端口", "node134", STANDBY_PORT),
        sql("按文档在提升后的 node134 新主写入业务数据", psql(STANDBY_PORT, "INSERT INTO public.%s VALUES(2,'2')" % TABLE), "返回 INSERT 0 1", "INSERT INTO public.%s VALUES(2,'2')" % TABLE),
        wait_for("等待 node135 从提升后的新主收到 id=2", PEER_PORT, "SELECT name FROM public.%s WHERE id=2" % TABLE, "2"), query("展示 node135 切换后的完整业务数据", PEER_PORT, "SELECT id,name FROM public.%s ORDER BY id" % TABLE, "返回 id=1、2、3", "1", "2", "3", "(3 rows)"),
    ],
    "teardown": "以 immediate 停止旧 node134 主库、已提升的新主及 node135 临时实例；递归删除主库、物理备库和 peer 数据目录，连同切换验证表、MMR 元数据、订阅、复制槽和本轮日志一并删除。",
}
