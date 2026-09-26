from suites.mmr.cases.conflict.delete_missing import query, setup, sql
from suites.mmr.cases.node_management.create_group import (PGCTL, PSQL, create_node,
                                                            init_instance, psql, shell)


ROOT = "/tmp/fbase_regress_mmr_physical_to_logical_{run_id}"
PRIMARY, STANDBY = ROOT + "/node134", ROOT + "/node137"
PRIMARY_PORT, STANDBY_PORT = "15571", "15572"
DSN = "host=127.0.0.1 port=%s user=postgres dbname=postgres" % PRIMARY_PORT
TABLE = "physical_to_logical_{run_id}"


def wait_for(title, port, statement, expected, timeout=40):
    return sql(title, "for i in $(seq 1 35); do v=$(%s -X -At -h 127.0.0.1 -p %s -U postgres -d postgres -c %r); test \"$v\" = %r && { echo \"$v\"; exit 0; }; sleep 1; done; exit 1" % (PSQL, port, statement, expected), "35 秒内输出 %s" % expected, statement, timeout)


CASE = {
    "id": "mmr.node_management.physical_to_logical_join",
    "name": "物理备库转换为普通逻辑复制订阅端",
    "document": "多活功能测试文档.md", "section": "9.8.1", "group": "node_management",
    "fixtures": ["cluster", {"type": "isolated_mmr_node_creation", "data_dir": ROOT}],
    "requirements": {"clusters": ["mmr"], "plugins": ["fdd_mmr"],
                     "commands": ["fdd_mmr_join"], "writable_node": True,
                     "node": "mmr:mmr1"}, "evidence_nodes": ["mmr:mmr1"],
    "test_topology": {"summary": "节点数=2；node134 普通主库，node137 先为其物理备库、后提升为普通逻辑订阅端。",
                      "nodes": [{"name":"node134","role":"普通 PostgreSQL 主库","host":"127.0.0.1","port":PRIMARY_PORT,"data_dir":PRIMARY},{"name":"node137","role":"物理备库 -> 普通逻辑订阅端","host":"127.0.0.1","port":STANDBY_PORT,"data_dir":STANDBY}],
                      "relations":["物理流复制: node134 -> node137（转换前）","普通逻辑复制: node134 -> node137（转换后）"]},
    "prerequisites": ["使用文档规定的 fdd_mmr_join 实际接口；物理备库在执行转换前停止。"],
    "steps": [
        setup(init_instance("初始化 node134 普通主库", PRIMARY, PRIMARY_PORT)),
        setup(shell("创建文档测试表、初始数据和 fdd_mmr 扩展", psql(PRIMARY_PORT, "CREATE TABLE public.%s(id serial PRIMARY KEY,data text); INSERT INTO public.%s(data) VALUES('seed1'),('seed2');" % (TABLE,TABLE)), "返回 CREATE TABLE 和 INSERT 0 2")),
        setup(shell("使用 pg_basebackup 创建 node137 物理备库", "mkdir -p %s; chmod 700 %s; /usr/local/fbase15.15/bin/pg_basebackup -h 127.0.0.1 -p %s -U postgres -D %s -R -X stream -c fast; cp %s/license.dat %s/license.dat; printf \"\\nport = %s\\nlisten_addresses = '127.0.0.1'\\n\" >> %s/postgresql.conf; %s -D %s -l %s/start.log -w start" % (STANDBY,STANDBY,PRIMARY_PORT,STANDBY,PRIMARY,STANDBY,STANDBY_PORT,STANDBY,PGCTL,STANDBY,STANDBY), "物理备库启动成功", timeout=90)),
        query("确认 node137 转换前为物理备库", STANDBY_PORT, "SELECT pg_is_in_recovery()", "返回 true", "t"),
        setup(shell("按文档停止 node137 物理备库", "%s -D %s stop -m fast" % (PGCTL,STANDBY), "物理备库停止", timeout=40)),
        sql("按文档执行 fdd_mmr_join 创建普通逻辑订阅", "/usr/local/fbase15.15/bin/fdd_mmr_join -U postgres -d postgres -D %s -P '%s' -p %s" % (STANDBY,DSN,STANDBY_PORT), "fdd_mmr_join 成功完成", "fdd_mmr_join -U postgres -d postgres -D %s -P '%s' -p %s" % (STANDBY,DSN,STANDBY_PORT), 120, report_node="node137"),
        sql("按文档启动已转换的 node137 逻辑订阅端", "%s -D %s -l %s/start.log -w start" % (PGCTL, STANDBY, STANDBY), "服务器启动成功", "pg_ctl -D %s -l %s/start.log -w start" % (STANDBY, STANDBY), 60, report_node="node137"),
        query("确认 node137 转换后已提升为可写订阅端", STANDBY_PORT, "SELECT pg_is_in_recovery()", "返回 false", "f"),
        query("展示 node137 的逻辑订阅", STANDBY_PORT, "SELECT subname,subenabled,subslotname FROM pg_subscription", "存在启用订阅和逻辑槽", "t"),
        sql("在 node134 写入转换后的业务数据", psql(PRIMARY_PORT, "INSERT INTO public.%s(data) VALUES('after_join')" % TABLE), "返回 INSERT 0 1", "INSERT INTO public.%s(data) VALUES('after_join');" % TABLE, report_node="node134"),
        wait_for("等待 node137 收到转换后的业务数据", STANDBY_PORT, "SELECT count(*) FROM public.%s WHERE data='after_join'" % TABLE, "1"),
        query("比较 node134/node137 转换后的数据条数", STANDBY_PORT, "SELECT count(*) FROM public.%s" % TABLE, "返回 3", "3"),
    ],
    "teardown": "以 immediate 停止 node134/node137 并删除临时目录，移除物理复制、逻辑订阅、逻辑槽和测试数据。",
}
