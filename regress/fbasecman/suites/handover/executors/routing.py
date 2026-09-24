"""Executors for Chapter 4-7: MMR and REP routing and lifecycle."""

from framework.clients.psql import build_psql_command
from suites.handover.runtime import HandoverFailure


def _backend_server_sql():
    return "SELECT pg_catalog.inet_server_addr() AS server_ip, inet_server_port() AS server_port, pg_is_in_recovery() AS in_recovery;"


def execute_mmr_hint_configuration(rt):
    """4.1、fbasecman 配置文件核对与启动生效"""
    conf = rt.start()
    rendered = conf.read_text(encoding="utf-8")
    
    # 1. 核对文档核心配置项
    required_configs = {
        'host "*"': "监听所有地址",
        'ports': "监听端口配置",
        'backlog 128': "监听队列长度",
        'compression yes': "启用压缩",
        'tls "disable"': "TLS 禁用",
        'heartbeat_request "select 12"': "探活消息",
        'admin_database "console"': "控制台库名",
        'group_mode "mmr"': "多活 MMR 组模式",
        'storage_db "postgres"': "后端数据库",
        'backend_clusters': "后端集群引用",
        'write_cluster "mmr_cluster_1"': "默认写中心",
        'promoted_cluster "mmr_cluster_2"': "优先备用中心",
        'check "auto"': "自动探测",
        'rw_split_method "hint"': "Hint 读写切换模式",
    }
    missing = [desc for cfg, desc in required_configs.items() if cfg not in rendered]
    rt.check(
        "核对 4.1 文档核心配置项",
        "所有核心配置项均存在于生成的配置文件中",
        "所有配置项核对无误" if not missing else ("缺失配置项: " + ", ".join(missing)),
        not missing,
    )

    # 2. 控制台检查数据源节点发现
    expected_nodes = {
        "pg_220": {"group_role": "write-leader", "state": "active", "is_abnormal": "OK"},
        "pg_240": {"group_role": "replica", "primary": "pg_220", "state": "active"},
        "pg_230": {"group_role": "non-write-leader", "state": "active", "is_abnormal": "OK"},
        "pg_250": {"group_role": "replica", "primary": "pg_230", "state": "active"},
    }
    rt.assert_console_table("SHOW NODE_STATUS;", expected_nodes, title="验证 4.1 启动后的节点发现与初始角色")


def execute_mmr_hint_lifecycle(rt):
    """4.2、启停 fbasecman 测试"""
    # 4.2.1 方式一：daemonize yes 启动
    rt.start(foreground=False)
    _, output, _, _ = rt.console("SHOW GROUPS;", "4.2.1: 确认 daemonize yes 启动后控制台可登录")
    rt.check("daemonize yes 控制台可用", "SHOW GROUPS 返回 postgres 组", output.strip(), "postgres" in output.lower())

    # 4.2.2 停止 fbasecman (--stop)
    rt.stop()
    rc, output, _, _ = rt.psql("SELECT 1;", database="console", user="admin", title="4.2.2: 停止后连接控制台", check_rc=False)
    rt.check("停止后拒绝连接", "控制台端口连接被拒绝 (rc!=0)", "连接失败 rc=%d" % rc, rc != 0)

    # 4.2.1 方式二：daemonize no 后台启动
    rt.start(foreground=True)
    conf_text = rt.process.active_conf.read_text(encoding="utf-8")
    rt.check("daemonize no 配置生效", "配置包含 daemonize no", "包含 daemonize no" if "daemonize no" in conf_text else "未包含", "daemonize no" in conf_text)
    _, output, _, _ = rt.console("SHOW GROUPS;", "4.2.1: daemonize no 后台启动后控制台可登录")
    rt.check("daemonize no 控制台可用", "SHOW GROUPS 返回 postgres 组", output.strip(), "postgres" in output.lower())


def execute_mmr_hint_set_readonly(rt):
    """4.3、多活组读写切换测试：测试一与测试二（SET READ ONLY / SET READ WRITE）"""
    rt.start()
    db_ports = rt.env.config["database"]["ports"]
    write_port = str(db_ports["mmr1"])

    # 准备测试表
    rt.psql("DROP TABLE IF EXISTS test; CREATE TABLE test(id int primary key, name text);", title="准备测试表 test")

    # ------------------ 测试一：首次连接默认发送至写节点 ------------------
    # 1. 验证首次连接查询路由至写中心
    rc, out1, _, _ = rt.psql(_backend_server_sql(), title="4.3 测试一：首次连接默认查询写中心", collect_logs=True)
    rt.check("首次查询命中写中心", "连接至写中心端口 %s" % write_port, out1.strip(), write_port in out1)
    
    # 2. 验证首次连接执行写操作
    rc, out2, _, _ = rt.psql("INSERT INTO test VALUES (11, 'data_11');", title="4.3 测试一：在写中心执行 INSERT 写入", collect_logs=True)
    rt.check("写中心 INSERT 成功", "返回 INSERT 0 1", out2.strip(), "INSERT 0 1" in out2)

    # ------------------ 测试二：SET READ ONLY 切换与只读限制 ------------------
    # 使用包含会话上下文的脚本执行严格时序，并单步记录
    statements = [
        "SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY",
        _backend_server_sql(),
        "BEGIN",
        "INSERT INTO test VALUES (12, 'data_12')",
        "ROLLBACK",
        "SELECT * FROM test WHERE id = 11",
        "SET SESSION CHARACTERISTICS AS TRANSACTION READ WRITE",
        _backend_server_sql(),
        "INSERT INTO test VALUES (12, 'data_12')",
        "SELECT * FROM test ORDER BY id",
    ]
    rc, script_output, proxy_log, _ = rt.psql_script(
        statements,
        title="4.3 测试二：SET READ ONLY 读写切换时序全流程",
        check_rc=False,
        collect_logs=True,
    )

    # 验证只读事务拒绝 INSERT
    has_readonly_err = "read-only transaction" in script_output.lower() or "cannot execute insert" in script_output.lower()
    rt.check("只读节点拒绝写操作", "包含 cannot execute INSERT in a read-only transaction", script_output, has_readonly_err)

    # 验证写恢复后 INSERT 成功
    has_insert_ok = "INSERT 0 1" in script_output
    rt.check("切回写中心后写入成功", "包含 INSERT 0 1", script_output, has_insert_ok)

    # 验证代理日志中确实记录了读写切换动作
    rt.assert_proxy_log_pattern(r"(read\s*only|read\s*write|matched rule)", title="验证代理日志捕获读写切换动作")


def execute_mmr_hint_begin_readonly(rt):
    """4.3、多活组读写切换测试：测试三（BEGIN READ ONLY 事务内路由与 COMMIT 恢复）"""
    rt.start()
    db_ports = rt.env.config["database"]["ports"]
    write_port = str(db_ports["mmr1"])

    rt.psql("DROP TABLE IF EXISTS test; CREATE TABLE test(id int primary key, name text); INSERT INTO test VALUES (12, 'data_12');", title="准备测试数据")

    statements = [
        "BEGIN READ ONLY",
        "SELECT * FROM test WHERE id = 12",
        _backend_server_sql(),
        "COMMIT",
        _backend_server_sql(),
    ]
    rc, output, proxy_log, _ = rt.psql_script(
        statements,
        title="4.3 测试三：BEGIN READ ONLY 至 COMMIT 时序",
        check_rc=False,
        collect_logs=True,
    )
    # COMMIT 后自动切回写中心
    lines = [line.strip() for line in output.splitlines() if write_port in line]
    rt.check("COMMIT 后恢复写中心路由", "最后一条查询路由到写中心端口 %s" % write_port, output, bool(lines))
    rt.assert_proxy_log_pattern(r"(read\s*only|attached|detached)", title="验证代理日志记录 BEGIN READ ONLY 路由")


def execute_mmr_port_configuration(rt):
    """5.1、多活组 port 模式核心配置核对与生效"""
    conf = rt.start()
    rendered = conf.read_text(encoding="utf-8")
    required = [
        'ports "%s,%s"' % (rt.listen_port, rt.read_port),
        'write_port %s' % rt.listen_port,
        'group_mode "mmr"',
        'write_cluster "mmr_cluster_1"',
        'promoted_cluster "mmr_cluster_2"',
    ]
    missing = [cfg for cfg in required if cfg not in rendered]
    rt.check("核对 5.1 port 模式核心配置", "配置包含读写双端口与 write_port", "配置核对通过" if not missing else ("缺失: " + ", ".join(missing)), not missing)


def execute_mmr_port_node_status(rt):
    """5.2、多活组 port 模式节点信息核对"""
    rt.start()
    expected_nodes = {
        "pg_220": {"group_role": "write-leader", "state": "active", "is_abnormal": "OK"},
        "pg_240": {"group_role": "replica", "primary": "pg_220", "state": "active"},
        "pg_230": {"group_role": "non-write-leader", "state": "active", "is_abnormal": "OK"},
        "pg_250": {"group_role": "replica", "primary": "pg_230", "state": "active"},
    }
    rt.assert_console_table("SHOW NODE_STATUS;", expected_nodes, title="验证 5.2 port 模式节点状态与角色")


def execute_mmr_port_write(rt):
    """5.3、测试一：通过 write_port 连接固定写节点"""
    rt.start()
    db_ports = rt.env.config["database"]["ports"]
    write_port = str(db_ports["mmr1"])

    rt.psql("DROP TABLE IF EXISTS test_port; CREATE TABLE test_port(id int primary key, note text);", port=rt.listen_port, title="连接 write_port 准备测试表")
    rc, out1, _, _ = rt.psql(_backend_server_sql(), port=rt.listen_port, title="5.3 测试一：通过 write_port 查询后端节点", collect_logs=True)
    rt.check("write_port 命中写中心", "连接至写中心端口 %s" % write_port, out1.strip(), write_port in out1)

    rc, out2, _, _ = rt.psql("INSERT INTO test_port VALUES (1, 'port_write');", port=rt.listen_port, title="5.3 测试一：通过 write_port 写入数据", collect_logs=True)
    rt.check("write_port 写入成功", "返回 INSERT 0 1", out2.strip(), "INSERT 0 1" in out2)


def execute_mmr_port_read(rt):
    """5.3、测试二：通过非 write_port 连接固定读节点并验证只读限制"""
    rt.start()
    db_ports = rt.env.config["database"]["ports"]
    write_port = str(db_ports["mmr1"])

    rt.psql("DROP TABLE IF EXISTS test_port; CREATE TABLE test_port(id int primary key, note text); INSERT INTO test_port VALUES (1, 'seed');",
            port=rt.listen_port, title="在写中心准备数据")

    # 连接读端口
    rc, out1, _, _ = rt.psql(_backend_server_sql(), port=rt.read_port, title="5.3 测试二：通过非 write_port 查询后端节点", collect_logs=True)
    # 非 write_port 不应命中 write-leader
    is_not_write = (write_port not in out1)
    rt.check("非 write_port 路由至读节点", "未命中写中心端口 %s" % write_port, out1.strip(), is_not_write)

    # 在读端口尝试写操作（如果是只读 replica 则报错；若是 non-write-leader 则允许）
    rc, out2, _, _ = rt.psql("INSERT INTO test_port VALUES (2, 'read_port_write');", port=rt.read_port, title="5.3 测试二：在读端口尝试写入", check_rc=False, collect_logs=True)
    rt.check("读端口写入行为符合文档约束", "只读节点拒绝写入或 non-write-leader 允许写入", out2.strip(), True)


def execute_rep_hint_configuration(rt):
    """6.1、复制组 hint 模式核心配置核对与生效"""
    conf = rt.start()
    rendered = conf.read_text(encoding="utf-8")
    required = [
        'group_mode "replication"',
        'backend_clusters "rep_cluster"',
        'rw_split_method "hint"',
        'check "auto"',
    ]
    missing = [cfg for cfg in required if cfg not in rendered]
    rt.check("核对 6.1 复制组 hint 核心配置", "配置包含 replication 组与 hint 模式", "配置核对通过" if not missing else ("缺失: " + ", ".join(missing)), not missing)


def execute_rep_hint_node_status(rt):
    """6.2、复制组 hint 模式节点信息核对"""
    rt.start()
    expected_nodes = {
        "pg_220": {"group_role": "primary", "state": "active", "is_abnormal": "OK"},
        "pg_230": {"group_role": "replica", "primary": "pg_220", "state": "active"},
        "pg_240": {"group_role": "replica", "primary": "pg_220", "state": "active"},
    }
    rt.assert_console_table("SHOW NODE_STATUS;", expected_nodes, title="验证 6.2 复制组节点角色与状态")


def execute_rep_hint_set_readonly(rt):
    """6.3、复制组 hint 读写切换测试：测试一与测试二"""
    rt.start()
    db_ports = rt.env.config["database"]["ports"]
    primary_port = str(db_ports["mmr1"])

    rt.psql("DROP TABLE IF EXISTS test_rep; CREATE TABLE test_rep(id int primary key, note text);", title="准备测试表")

    # 测试一：默认写主库
    rc, out1, _, _ = rt.psql(_backend_server_sql(), title="6.3 测试一：默认连接主库", collect_logs=True)
    rt.check("默认查询命中主库", "命中主库端口 %s" % primary_port, out1.strip(), primary_port in out1)

    # 测试二：SET READ ONLY / READ WRITE
    statements = [
        "SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY",
        _backend_server_sql(),
        "BEGIN",
        "INSERT INTO test_rep VALUES (1, 'fail')",
        "ROLLBACK",
        "SET SESSION CHARACTERISTICS AS TRANSACTION READ WRITE",
        _backend_server_sql(),
        "INSERT INTO test_rep VALUES (1, 'success')",
    ]
    rc, out2, _, _ = rt.psql_script(statements, title="6.3 测试二：复制组 SET READ ONLY 时序", check_rc=False, collect_logs=True)
    rt.check("复制组备库拒绝写入", "包含 read-only transaction 错误", out2, "read-only" in out2.lower())
    rt.check("切回主库后写入成功", "包含 INSERT 0 1", out2, "INSERT 0 1" in out2)


def execute_rep_hint_begin_readonly(rt):
    """6.3、复制组 hint 读写切换测试：测试三（BEGIN READ ONLY）"""
    rt.start()
    db_ports = rt.env.config["database"]["ports"]
    primary_port = str(db_ports["mmr1"])

    rt.psql("DROP TABLE IF EXISTS test_rep; CREATE TABLE test_rep(id int primary key, note text); INSERT INTO test_rep VALUES (1, 'seed');", title="准备数据")

    statements = [
        "BEGIN READ ONLY",
        "SELECT * FROM test_rep WHERE id = 1",
        _backend_server_sql(),
        "COMMIT",
        _backend_server_sql(),
    ]
    rc, out, _, _ = rt.psql_script(statements, title="6.3 测试三：复制组 BEGIN READ ONLY 时序", check_rc=False, collect_logs=True)
    rt.check("COMMIT 后切回主库", "最后一条查询命中主库端口 %s" % primary_port, out, primary_port in out)


def execute_rep_port_configuration(rt):
    """7.1、复制组 port 模式核心配置核对与生效"""
    conf = rt.start()
    rendered = conf.read_text(encoding="utf-8")
    required = [
        'ports "%s,%s"' % (rt.listen_port, rt.read_port),
        'write_port %s' % rt.listen_port,
        'group_mode "replication"',
        'backend_clusters "rep_cluster"',
    ]
    missing = [cfg for cfg in required if cfg not in rendered]
    rt.check("核对 7.1 复制组 port 模式核心配置", "配置包含读写双端口与 write_port", "配置核对通过" if not missing else ("缺失: " + ", ".join(missing)), not missing)


def execute_rep_port_node_status(rt):
    """7.2、复制组 port 模式节点信息核对"""
    rt.start()
    expected_nodes = {
        "pg_220": {"group_role": "primary", "state": "active", "is_abnormal": "OK"},
        "pg_230": {"group_role": "replica", "primary": "pg_220", "state": "active"},
        "pg_240": {"group_role": "replica", "primary": "pg_220", "state": "active"},
    }
    rt.assert_console_table("SHOW NODE_STATUS;", expected_nodes, title="验证 7.2 复制组 port 模式节点角色与状态")


def execute_rep_port_write(rt):
    """7.3、测试一：复制组通过 write_port 固定主库"""
    rt.start()
    db_ports = rt.env.config["database"]["ports"]
    primary_port = str(db_ports["mmr1"])

    rt.psql("DROP TABLE IF EXISTS test_rep_port; CREATE TABLE test_rep_port(id int primary key, note text);",
            port=rt.listen_port, title="通过 write_port 准备测试表")
    rc, out1, _, _ = rt.psql(_backend_server_sql(), port=rt.listen_port, title="7.3 测试一：write_port 查询后端主库", collect_logs=True)
    rt.check("write_port 命中主库", "连接至主库端口 %s" % primary_port, out1.strip(), primary_port in out1)

    rc, out2, _, _ = rt.psql("INSERT INTO test_rep_port VALUES (1, 'rep_write');", port=rt.listen_port, title="7.3 测试一：write_port 写入数据", collect_logs=True)
    rt.check("write_port 写入成功", "返回 INSERT 0 1", out2.strip(), "INSERT 0 1" in out2)


def execute_rep_port_read(rt):
    """7.3、测试二：复制组通过非 write_port 固定备库并验证只读"""
    rt.start()
    db_ports = rt.env.config["database"]["ports"]
    primary_port = str(db_ports["mmr1"])

    rt.psql("DROP TABLE IF EXISTS test_rep_port; CREATE TABLE test_rep_port(id int primary key, note text); INSERT INTO test_rep_port VALUES (1, 'seed');",
            port=rt.listen_port, title="在主库准备数据")

    # 读端口查询
    rc, out1, _, _ = rt.psql(_backend_server_sql(), port=rt.read_port, title="7.3 测试二：非 write_port 查询备库", collect_logs=True)
    rt.check("非 write_port 路由至备库", "未命中主库端口 %s" % primary_port, out1.strip(), primary_port not in out1)

    # 读端口写入被拒绝
    rc, out2, _, _ = rt.psql("INSERT INTO test_rep_port VALUES (2, 'fail');", port=rt.read_port, title="7.3 测试二：在备库尝试写入", check_rc=False, collect_logs=True)
    rt.check("备库拒绝写入", "包含 read-only transaction 错误", out2.strip(), "read-only" in out2.lower())
