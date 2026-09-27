from suites.mmr.cases.conflict.delete_missing import query, setup, sql
from suites.mmr.cases.node_management.create_group import (PGCTL, PSQL, create_node,
                                                            init_instance, psql, shell)

ROOT = "/tmp/fbase_regress_mmr_physical_to_mmr_{run_id}"
NODE34, NODE35, NODE37 = ROOT + "/node134", ROOT + "/node135", ROOT + "/node137"
PORT34, PORT35, PORT37 = "15574", "15575", "15576"
DSN34 = "host=127.0.0.1 port=%s user=postgres dbname=postgres" % PORT34
DSN37 = "host=127.0.0.1 port=%s user=postgres dbname=postgres" % PORT37
T1, T2 = "physical_mmr_t1_{run_id}", "physical_mmr_t2_{run_id}"

def wait_for(title, port, statement, expected, timeout=45):
    return sql(title, "for i in $(seq 1 40); do v=$(%s -X -At -h 127.0.0.1 -p %s -U postgres -d postgres -c %r); test \"$v\" = %r && { echo \"$v\"; exit 0; }; sleep 1; done; exit 1" % (PSQL, port, statement, expected), "40 秒内输出 %s" % expected, statement, timeout)

CASE = {
 "id":"mmr.node_management.physical_to_mmr_join", "name":"物理备库转换后自动加入多活组", "document":"多活功能测试文档.md", "section":"9.8.2", "group":"node_management",
 "fixtures":["cluster", {"type":"isolated_mmr_node_creation","data_dir":ROOT}],
 "requirements":{"clusters":["mmr"],"plugins":["fdd_mmr"],"commands":["fdd_mmr_join"],"writable_node":True,"node":"mmr:mmr1"}, "evidence_nodes":["mmr:mmr1"],
 "test_topology":{"summary":"节点数=3；node134/node135 为对等 MMR 节点，node137 先为 node134 物理备库、后自动加入 MMR。","nodes":[{"name":"node134","role":"MMR primary","host":"127.0.0.1","port":PORT34,"data_dir":NODE34},{"name":"node135","role":"MMR primary","host":"127.0.0.1","port":PORT35,"data_dir":NODE35},{"name":"node137","role":"物理备库 -> MMR primary","host":"127.0.0.1","port":PORT37,"data_dir":NODE37}],"relations":["MMR 多活: node134 <-> node135，转换后加入 node137","物理流复制: node134 -> node137（转换前）"]},
 "prerequisites":["转换前只允许 node134 在线写入；node135 在 node137 加入期间不写入，符合文档在线 join 限制。"],
 "steps":[
  setup(init_instance("初始化 node134",NODE34,PORT34)),setup(create_node(PORT34,"node34")),setup(shell("创建文档两张测试表和初始数据",psql(PORT34,"CREATE TABLE public.%s(id serial PRIMARY KEY,data text); CREATE TABLE public.%s(id serial PRIMARY KEY,data text); INSERT INTO public.%s(data) VALUES('seed1'),('seed2'); INSERT INTO public.%s(data) VALUES('seed1')"%(T1,T2,T1,T2)),"返回两次 CREATE TABLE 和 INSERT")),setup(shell("创建 g1 多活组",psql(PORT34,"SELECT fdd.create_group('g1')"),"创建成功")),
  setup(init_instance("初始化 node135",NODE35,PORT35)),setup(create_node(PORT35,"node35")),setup(shell("按文档将 node135 以 all 模式加入 g1",psql(PORT35,"SELECT fdd.join_group('g1','%s',true,'all','table_exist_error')"%DSN34),"加入成功",timeout=90)),
  setup(shell("使用 pg_basebackup 创建 node137 物理备库", "mkdir -p %s; chmod 700 %s; /usr/local/fbase15.15/bin/pg_basebackup -h 127.0.0.1 -p %s -U postgres -D %s -R -X stream -c fast; cp %s/license.dat %s/license.dat; printf \"\\nport = %s\\nlisten_addresses = '127.0.0.1'\\n\" >> %s/postgresql.conf; %s -D %s -l %s/start.log -w start"%(NODE37,NODE37,PORT34,NODE37,NODE34,NODE37,PORT37,NODE37,PGCTL,NODE37,NODE37),"物理备库启动成功",timeout=90)),
  query("确认 node137 转换前为物理备库",PORT37,"SELECT pg_is_in_recovery()","返回 true","t"),setup(shell("按文档停止 node137 物理备库", "%s -D %s stop -m fast"%(PGCTL,NODE37),"已停止")),
  sql("按文档执行 fdd_mmr_join 自动加入 g1", "/usr/local/fbase15.15/bin/fdd_mmr_join -U postgres -d postgres -D %s -P '%s' -p %s -M node37 -L '%s' -A"%(NODE37,DSN34,PORT37,DSN37),"fdd_mmr_join 成功完成","fdd_mmr_join -U postgres -d postgres -D %s -P '%s' -p %s -M node37 -L '%s' -A"%(NODE37,DSN34,PORT37,DSN37),180,report_node="node137"),
  sql("按文档启动已转换的 node137 多活节点","%s -D %s -l %s/start.log -w start"%(PGCTL,NODE37,NODE37),"服务器启动成功","pg_ctl -D %s -l %s/start.log -w start"%(NODE37,NODE37),60,report_node="node137"),
  query("确认 node137 转换后为可写多活节点",PORT37,"SELECT pg_is_in_recovery()","返回 false","f"),
  query("确认 node137 的三成员多活元数据均 ACTIVE",PORT37,"SELECT count(*)::text,bool_and(node_state='ACTIVE')::text FROM fdd.mmr_node","返回 3|true","3","true"),
  query("展示 node134 自动 join 后的完整多活元数据",PORT34,"SELECT node_id,node_name,node_state,node_dsn FROM fdd.mmr_node ORDER BY node_id","应包含 node34、node35、node37 三个 ACTIVE 节点","node34","node35","node37"),
  query("展示 node134 自动 join 后的完整集群校验明细",PORT34,"SELECT * FROM fdd.show_node_info(true,false)","应仅有三个 ACTIVE/OK 节点","node34","node35","node37"),
  wait_for("等待 node137 追增完成且 node134 集群校验恢复正常",PORT34,"SELECT count(*)::text || '|' || bool_and(is_abnormal='OK')::text FROM fdd.show_node_info(true,false)","3|true",60),
  query("确认 node134 集群校验三成员均正常",PORT34,"SELECT count(*)::text,bool_and(is_abnormal='OK')::text FROM fdd.show_node_info(true,false)","返回 3|true","3","true"),
  sql("在 node134 写入转换后的业务数据",psql(PORT34,"INSERT INTO public.%s(data) VALUES('after_join')"%T1),"返回 INSERT 0 1","INSERT INTO public.%s(data) VALUES('after_join');"%T1,report_node="node134"),
  wait_for("等待 node137 收到转换后的多活业务数据",PORT37,"SELECT count(*) FROM public.%s WHERE data='after_join'"%T1,"1"),
 ],
 "teardown":"以 immediate 停止 node134/node135/node137 并删除临时目录，清理物理复制、MMR 订阅、槽和测试数据。",
}
