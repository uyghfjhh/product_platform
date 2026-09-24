"""Coverage inventory for doc/转测文档/fbasecman转测文档.md business chapters 4-11.

Environment construction (chapters 1-3 and every "测试环境" subsection) is
intentionally excluded.  A source section occurs once only; multiple SQL
operations documented under that section are verification steps of one case.
"""

import re

from suites.handover.case import HandoverCase


def _contents(name, summary, sections, executor, topology):
    """Document-faithful test content shown at the top of each report.

    These are test objectives from the cited transfer-document section, not
    implementation notes.  Setup-only document paragraphs are deliberately
    not represented as business test content.
    """
    group = "MMR" if topology == "mmr" else "REP"
    by_executor = {
        "configuration": (
            "按文档核心配置启动 %s %s 模式，核对监听、数据源、组模式和路由配置。" % (group, "hint/port"),
            "启动后通过 SHOW NODE_STATUS 确认配置的节点信息可被识别。",
        ),
        "lifecycle": (
            "配置 daemonize 为 yes 后可启动，并可登录 fbasecman 控制台。",
            "使用 --stop 停止后，控制台端口拒绝连接。",
            "配置 daemonize 为 no 且以 & 启动后，可登录控制台。",
        ),
        "hint_set": (
            "首次连接及未设置读写标签时，SQL 路由到写节点。",
            "SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY 与 READ WRITE 之间的 SQL 保持在同一目标节点。",
            "%s 读节点的读写能力符合文档：仅 non-write-leader 可读写，其余读节点只读。" % group,
        ),
        "hint_begin": (
            "BEGIN READ ONLY 至 COMMIT、ROLLBACK 或 END 之间的 SQL 保持在同一读节点；结束后恢复默认写路由。",
        ),
        "node_status": (
            "登录控制台执行 SHOW NODE_STATUS，查看当前组的节点信息。",
        ),
        "port_write": (
            "连接 write_port 后，连接期间的读写 SQL 均发送至写节点且连接不切换。",
        ),
        "port_read": (
            "连接非 write_port 后，连接期间 SQL 路由到读节点且连接不切换。",
            "%s 读节点的读写能力符合文档约束。" % group,
        ),
        "ha": (
            "模拟文档指定节点故障，并通过 SHOW NODE_STATUS 确认故障节点状态。",
            "按文档修改 parted/promoted 或降级配置并 RELOAD，使故障节点不再参与路由。",
            "验证故障处理后的读写路由、可用性和只读限制。",
        ),
        "jdbc": (
            "按文档 JDBC 驱动版本和 SQL 时序执行第 11 组读写标签场景。",
            "按文档 JDBC 驱动版本和 SQL 时序执行第 12 组读写标签场景。",
            "按文档 JDBC 驱动版本和 SQL 时序执行第 13 组读写标签场景。",
            "按文档 JDBC 驱动版本和 SQL 时序执行第 14 组读写标签场景。",
        ),
        "console_metadata": (
            "SHOW HELP 返回控制台帮助。",
            "SHOW GROUPS 返回组信息。",
            "SHOW GROUP_MEMBERS 返回组成员信息。",
            "SHOW NODES 返回节点信息。",
            "SHOW NODE_STATUS 返回节点状态信息。",
        ),
        "console_maintenance": (
            "SPLITLOG 可切换日志文件。",
            "DROP SERVERS 可清理全部后端 server。",
            "DROP SERVER <ptr> 可清理指定后端 server。",
            "DROP UNUSE_SERVERS 可清理未使用后端 server。",
        ),
        "console_stats": (
            "SHOW SERVERS 展示 server 统计字段，并在业务请求后反映读写请求统计。",
        ),
        "console_thread_pool_stats": (
            "压测运行中 SHOW THREAD_STATUS 展示线程连接数、读写比例和 QPS。",
            "压测运行中 SHOW POOLS 展示连接池请求统计。",
            "压测结束后线程和连接池统计回到稳定状态。",
        ),
        "console_reset_stats": (
            "RESET ALL、TIME、REQUEST、BYTE、CONNECT 的各目标类型按文档生效。",
            "压测运行中 RESET REQUEST CLIENTS 仅重置 client 请求统计。",
            "压测结束后 RESET ALL 重置统计。",
        ),
        "long_statistics": (
            "按文档规定的 pgbench SQL、客户端数和持续时间产生业务流量。",
            "运行中及结束后 SHOW SERVERS/CLIENTS/THREAD_STATUS/POOLS 的统计与 workload 事务数一致。",
        ),
        "heartbeat": (
            "配置的 heartbeat_request 被 fbasecman 拦截且不发送到 PostgreSQL。",
            "普通 SQL 和带读写标签的 heartbeat 邻接语句仍按文档路由。",
        ),
        "guc_sync": (
            "开启 GUC 同步后，设置、RESET/RESET ALL/DISCARD ALL 与读写切换保持正确会话状态。",
            "关闭 GUC 同步及文档列出的 Report GUC、DateStyle 格式下，读写路由保持正确。",
            "PreparedStatement 复用时 GUC 状态不串联，事务提交正常。",
        ),
        "attach": (
            "attach 后读写切换标签不产生多余后端连接。",
            "attach 后 heartbeat 不产生多余后端连接。",
            "attach 后设置 GUC 不产生多余后端连接。",
        ),
        "parse_error": (
            "Parse 执行失败后 JDBC 连接可继续使用，后续 PreparedStatement 可正常执行。",
            "fbasecman 清理失败 Parse 对应的后端缓存，避免缓存残留影响后续请求。",
        ),
        "global_ps_special": (
            "心跳、GUC 和读写事务标签的 PreparedStatement 分类、路由和全局缓存信息正确。",
        ),
        "global_ps_eviction": (
            "后端和全局 PreparedStatement 缓存达到容量后按文档淘汰，统计和保留 SQL 正确。",
        ),
        "global_ps_bypass": (
            "含 bypass 响应的全局 PreparedStatement 缓存条目不被淘汰，统计和普通 SQL 缓存正确。",
        ),
    }
    return by_executor[executor]


def _rules(executor):
    """Stable-title rules for report-step to document-content association."""
    setup = (
        (r"^(启动 fbasecman|在 .*准备|准备 |创建 |建立 |重建 |.*文档 pgbench SQL|启动 pgbench)", "前置条件", "搭建本用例所需的独立代理、数据或负载环境"),
        (r"^(PG 节点start|删除 .*临时角色)", "环境恢复", "恢复本用例修改的 PostgreSQL 节点或临时对象"),
    )
    rules = {
        "configuration": ((r"核对文档核心配置", "1", "核对文档列出的核心配置项"),
                            (r"验证配置启动后的节点信息", "2", "控制台确认配置已生效并发现节点")),
        "lifecycle": ((r"确认控制台可登录", "1", "daemonize=yes 启动后控制台可登录"),
                      (r"停止后连接控制台|停止后拒绝连接", "2", "--stop 后控制台拒绝连接"),
                      (r"daemonize no", "3", "daemonize=no + & 启动后控制台可登录")),
        "hint_set": ((r"SET READ ONLY/READ WRITE", "1、2、3", "默认、READ ONLY、READ WRITE 的路由和读写能力"),),
        "hint_begin": ((r"BEGIN READ ONLY", "1", "BEGIN READ ONLY 至结束命令期间的路由保持及结束后恢复"),),
        "node_status": ((r"节点状态", "1", "SHOW NODE_STATUS 返回当前组节点信息"),),
        "port_write": ((r"通过 write_port", "1", "write_port 路由写节点并可读写"),),
        "port_read": ((r"非 write_port|MMR 第 .*read_port", "1、2", "非 write_port 路由读节点、连接保持及读写限制"),),
        "ha": ((r"PG 节点stop|故障解决方案前.*SHOW NODE_STATUS|故障后的 SHOW NODE_STATUS|故障发生后|模拟.*故障|单次探测失败|停止.*节点", "1", "模拟故障并确认控制台发现故障节点"),
               (r"修改数据源状态|修改故障切换配置|删除冲突的故障切换配置|RELOAD|应用故障解决方案后.*SHOW NODE_STATUS|重启 fbasecman 加载降级配置|D-001 文档降级拓扑|重启后的只读 replication 节点状态|降级配置|SET NODE PARTED|SET NODE ACTIVE|REFRESH CLUSTER|PG 节点promote|pause_replay|resume_replay|确认故障发布|升主", "2", "应用文档故障解决配置或监控门禁并确认节点状态"),
               (r"默认写路由|读写 SQL|读连接|读写切换|只读 SQL|显式写切换|读写时序|验证.*路由|业务读写|重试计数|级联屏蔽|延迟屏蔽|回退|只读事务|写操作", "3", "验证故障处理后的路由、DML 或只读限制")),
        "jdbc": ((r"OLD_11|NEW_11", "1", "执行文档第 11 组 JDBC 时序并观察路由"),
                 (r"OLD_12|NEW_12", "2", "执行文档第 12 组 JDBC 时序并观察路由"),
                 (r"OLD_13|NEW_13", "3", "执行文档第 13 组 JDBC 时序并观察路由"),
                 (r"OLD_14|NEW_14", "4", "执行文档第 14 组 JDBC 时序并观察路由")),
        "console_metadata": ((r"SHOW HELP", "1", "执行并检查 SHOW HELP"), (r"SHOW GROUPS", "2", "执行并检查 SHOW GROUPS"),
                              (r"SHOW GROUP_MEMBERS", "3", "执行并检查 SHOW GROUP_MEMBERS"),
                              (r"SHOW NODES", "4", "执行并检查 SHOW NODES"),
                              (r"SHOW NODE_STATUS", "5", "执行并检查 SHOW NODE_STATUS")),
        "console_maintenance": ((r"SPLITLOG", "1", "执行并检查 SPLITLOG"), (r"DROP SERVERS", "2", "执行并检查 DROP SERVERS"),
                                 (r"DROP SERVER(?: |$)", "3", "执行并检查 DROP SERVER <ptr>"), (r"DROP UNUSE_SERVERS", "4", "执行并检查 DROP UNUSE_SERVERS"),
                                 (r"PG 节点stop", "4", "停止目标 PostgreSQL 节点，使 idle server 可被失效探测"),
                                 (r"SHOW SERVERS|后端 server", "1 至 4", "为对应清理命令建立或检查 server 状态")),
        "console_stats": ((r"SHOW SERVERS|pgbench", "1", "产生业务请求并检查 SHOW SERVERS 统计"),),
        "console_thread_pool_stats": ((r"THREAD_STATUS", "1、3", "检查压测中及结束后的线程统计"), (r"SHOW POOLS", "2、3", "检查压测中及结束后的连接池统计"),
                                       (r"等待 pgbench", "1、2、3", "等待文档压测完成")),
        "console_reset_stats": ((r"10\.2\.5.*RESET|H-001 .*RESET|压测结束后 RESET ALL|RESET REQUEST CLIENTS|RESET ALL 后", "1 至 3", "执行 RESET 命令并检查相应统计范围"),
                                  (r"SHOW (SERVERS|CLIENTS|THREAD_STATUS|POOLS)|pgbench", "1 至 3", "产生并读取 RESET 前后的统计")),
        "long_statistics": ((r"SHOW |pgbench|workload", "1、2", "执行文档 workload 并对账控制台统计"),),
        "heartbeat": ((r"精确 heartbeat", "1、2", "验证精确探活拦截、普通 SQL 和标签路由"), (r"空格不同", "1", "验证不完全匹配的 SQL 不被误拦截")),
        "guc_sync": ((r"GUC 同步|GUC RESET|DISCARD ALL|Report GUC|DateStyle", "1、2", "执行文档 GUC 设置、重置和路由时序"),
                     (r"HandoverGucReuse", "3", "观察 PreparedStatement 复用中的 GUC 隔离和提交")),
        "attach": ((r"读写切换标签", "1", "比较标签前后的 server 数量"), (r"heartbeat", "2", "比较 heartbeat 前后的 server 数量"),
                   (r"GUC", "3", "比较 GUC 设置前后的 server 数量"), (r"SHOW SERVERS", "1 至 3", "读取对应 attach 场景的 server 状态")),
        "parse_error": ((r"Parse 失败后立即检查后端 PreparedStatement", "2", "确认失败 SQL 未残留在后端 PreparedStatement 缓存"),
                        (r"Parse 失败后|PreparedStatement", "1", "失败 Parse 后继续执行 PreparedStatement"),
                        (r"报文与后端缓存清理日志", "2", "从定向日志窗口确认失败 Parse 的后端缓存清理")),
        "global_ps_special": ((r"HandoverGlobalCache|特殊 SQL", "1", "观察特殊 SQL 的分类、路由和全局缓存"),),
        "global_ps_eviction": ((r"HandoverGlobalCache|缓存", "1", "观察容量淘汰、统计和保留 SQL"),),
        "global_ps_bypass": ((r"HandoverGlobalCache|bypass", "1", "观察 bypass 条目保留、统计和普通 SQL 缓存"),),
    }
    return setup + rules[executor]


def _case(name, summary, sections, executor, **kwargs):
    topology = kwargs.get("topology")
    if "notes" not in kwargs:
        kwargs["notes"] = _contents(name, summary, sections, executor, topology)
    if "step_mapping" not in kwargs:
        kwargs.setdefault("step_rules", _rules(executor))
    return HandoverCase(name, summary, sections, executor, **kwargs)


HANDOVER_CASES = [
    # 4-7: group configuration, lifecycle and routing contracts.
    _case("mmr_hint_configuration", "MMR hint 核心配置渲染和生效", ("4.1",), "configuration", topology="mmr", route_mode="hint"),
    _case("mmr_hint_lifecycle", "MMR hint 模式启动、控制台可用和停止", ("4.2.1", "4.2.2"), "lifecycle", topology="mmr", route_mode="hint"),
    _case("mmr_hint_set_readonly", "MMR hint 的 SET READ ONLY/WRITE 时序", ("4.3-测试一", "4.3-测试二"), "hint_set", topology="mmr", route_mode="hint"),
    _case("mmr_hint_begin_readonly", "MMR hint 的 BEGIN READ ONLY/COMMIT 时序", ("4.3-测试三",), "hint_begin", topology="mmr", route_mode="hint"),
    _case("mmr_port_configuration", "MMR port 核心配置渲染和生效", ("5.1",), "configuration", topology="mmr", route_mode="port"),
    _case("mmr_port_node_status", "MMR port 节点信息", ("5.2",), "node_status", topology="mmr", route_mode="port"),
    _case("mmr_port_write", "MMR write_port 固定写节点", ("5.3-测试一",), "port_write", topology="mmr", route_mode="port"),
    _case("mmr_port_read", "MMR 非 write_port 两类读节点行为", ("5.3-测试二",), "port_read", topology="mmr", route_mode="port", issue_id="H-002"),
    _case("rep_hint_configuration", "REP hint 核心配置渲染和生效", ("6.1",), "configuration", topology="rep", route_mode="hint"),
    _case("rep_hint_node_status", "REP hint 节点信息", ("6.2",), "node_status", topology="rep", route_mode="hint"),
    _case("rep_hint_set_readonly", "REP hint 的 SET READ ONLY/WRITE 时序", ("6.3-测试一", "6.3-测试二"), "hint_set", topology="rep", route_mode="hint"),
    _case("rep_hint_begin_readonly", "REP hint 的 BEGIN READ ONLY/COMMIT 时序", ("6.3-测试三",), "hint_begin", topology="rep", route_mode="hint"),
    _case("rep_port_configuration", "REP port 核心配置渲染和生效", ("7.1",), "configuration", topology="rep", route_mode="port"),
    _case("rep_port_node_status", "REP port 节点信息", ("7.2",), "node_status", topology="rep", route_mode="port"),
    _case("rep_port_write", "REP write_port 固定主节点", ("7.3-测试一",), "port_write", topology="rep", route_mode="port"),
    _case("rep_port_read", "REP 非 write_port 固定只读节点", ("7.3-测试二",), "port_read", topology="rep", route_mode="port"),
    # 8: each failure solution plus the documented routing verification (8.2~8.8).
    _case("ha_write_leader_failure", "write-leader 故障后的提升和读写路由", ("8.2.2", "8.2.3"), "ha", topology="mmr"),
    _case("ha_non_write_leader_failure", "non-write-leader 故障后的读写路由", ("8.3.2", "8.3.3"), "ha", topology="mmr"),
    _case("ha_all_mmr_failure", "全部 MMR 节点故障后的只读降级与写拒绝", ("8.4.2", "8.4.3"), "ha", topology="mmr"),
    _case("ha_replica_failure", "备库故障后的读节点排除", ("8.5.2", "8.5.3"), "ha", topology="mmr"),
    _case("ha_non_write_leader_family_failure", "非 write-leader 全部故障后的单节点路由", ("8.6.2", "8.6.3"), "ha", topology="mmr"),
    # 8.7 复制组高可用场景
    _case("ha_rep_primary_failure", "复制组 primary 故障但尚未升主", ("8.7.2",), "ha", topology="ha_rep"),
    _case("ha_rep_promote", "复制组 replica 升主并恢复读写服务", ("8.7.3",), "ha", topology="ha_rep"),
    _case("ha_rep_replica_failure", "复制组单个 replica 故障和恢复", ("8.7.4",), "ha", topology="ha_rep"),
    _case("ha_rep_all_failure", "复制组 primary 和 replica 全部故障", ("8.7.5",), "ha", topology="ha_rep"),
    _case("ha_rep_old_primary_recovery", "复制组旧 primary 以 replica 身份回归", ("8.7.6",), "ha", topology="ha_rep"),
    _case("ha_rep_port_and_sql_parse", "复制组 Port 和 SQL 语法解析模式高可用", ("8.7.7",), "ha", topology="ha_rep"),
    # 8.8 监控自动化测试
    _case("ha_monitor_single_failure_retry", "监控单次失败不得立即下线（重试门禁）", ("8.8.2",), "ha", topology="ha_rep"),
    _case("ha_monitor_rep_replica_confirm", "复制组备库确认故障和恢复", ("8.8.3",), "ha", topology="ha_rep"),
    _case("ha_monitor_rep_primary_promote", "复制组 primary 故障、升主和旧主回归", ("8.8.4",), "ha", topology="ha_rep"),
    _case("ha_monitor_mmr_cascade_blocked", "MMR 写中心故障、级联屏蔽和 promoted 接管", ("8.8.5",), "ha", topology="mmr"),
    _case("ha_monitor_wal_lag_block", "WAL 字节延迟屏蔽", ("8.8.6",), "ha", topology="ha_rep"),
    _case("ha_monitor_manual_isolation", "人工隔离 PARTED、ACTIVE 和显式复探 REFRESH", ("8.8.7",), "ha", topology="ha_rep"),
    # 9: the document's JDBC examples are all MMR hint-mode examples.  Do not
    # invent REP source headings merely because the runtime can support REP.
    _case("jdbc_4227_mmr_hint", "JDBC 42.2.7 MMR hint 必测和补充时序", ("9.1.1", "9.1.2"), "jdbc", topology="mmr", route_mode="hint"),
    _case("jdbc_4270_mmr_hint", "JDBC 42.7.0 MMR hint 必测和补充时序", ("9.2.1/42.7.0",), "jdbc", topology="mmr", route_mode="hint"),
    _case("jdbc_4277_mmr_hint", "JDBC 42.7.7 MMR hint 必测和补充时序", ("9.2.2/42.7.7", "9.2.2-补充测试"), "jdbc", topology="mmr", route_mode="hint"),
    # 10: console operations and statistics.  The hour-long traffic examples are separate.
    _case("console_group_metadata", "SHOW HELP/GROUPS/GROUP_MEMBERS/NODES/NODE_STATUS", ("10.1.1", "10.1.2", "10.1.3", "10.1.4", "10.1.5"), "console_metadata", topology="mmr", route_mode="hint"),
    _case("console_server_maintenance", "SPLITLOG 和 server 清理命令", ("10.1.6", "10.1.7", "10.1.8", "10.1.9"), "console_maintenance", topology="mmr", route_mode="hint"),
    _case("console_statistics", "SHOW SERVERS 基础字段和读写统计", ("10.2.1",), "console_stats", topology="mmr", route_mode="hint"),
    _case("console_thread_pool_statistics", "250 client 下 SHOW THREAD_STATUS 与 SHOW POOLS", ("10.2.3", "10.2.3.1", "10.2.3.2", "10.2.4", "10.2.4.1", "10.2.4.2"), "console_thread_pool_stats", topology="mmr", route_mode="hint"),
    _case("console_reset_statistics", "统计信息 RESET 类型和目标范围", ("10.2.5", "10.2.5.1", "10.2.5.2"), "console_reset_stats", topology="mmr", route_mode="hint", issue_id="H-001"),
    _case("console_server_lifecycle_statistics", "SHOW SERVERS 30 分钟生命周期和流量统计", ("10.2.1.2",), "long_statistics", topology="mmr", route_mode="hint", long_time=True, workload="server_lifecycle"),
    _case("console_client_statistics", "SHOW CLIENTS 30 分钟长短连接统计", ("10.2.2.1", "10.2.2.2"), "long_statistics", topology="mmr", route_mode="hint", long_time=True, workload="clients"),
    _case("console_pgbench_mmr_hint_statistics", "可靠性 pgbench：MMR hint 请求计数", ("10.2.1.1/mmrhint",), "long_statistics", topology="mmr", route_mode="hint", long_time=True, workload="mmr_hint"),
    _case("console_pgbench_mmr_port_statistics", "可靠性 pgbench：MMR port 请求计数", ("10.2.1.1/mmrport",), "long_statistics", topology="mmr", route_mode="port", long_time=True, workload="mmr_port"),
    _case("console_pgbench_rep_hint_statistics", "可靠性 pgbench：REP hint 请求计数", ("10.2.1.1/rephint",), "long_statistics", topology="rep", route_mode="hint", long_time=True, workload="rep_hint"),
    _case("console_pgbench_balance_statistics", "可靠性 pgbench：balance 权重分流", ("10.2.1.1/balance",), "long_statistics", topology="balance", route_mode="hint", long_time=True, workload="balance"),
    # 11: expanded product capabilities.
    _case("heartbeat_interception", "探活 SQL 拦截且不发送至 PG", ("11.1",), "heartbeat", topology="mmr", route_mode="hint"),
    _case("guc_sync", "GUC 感知、同步和 reload", ("11.2.1", "11.2.2"), "guc_sync", topology="mmr", route_mode="hint"),
    _case("attach_optimization", "attach 下的读写标签、探活和 GUC", ("11.3.2", "11.3.3", "11.3.4"), "attach", topology="mmr", route_mode="hint"),
    _case("parse_error_single", "单 Parse 失败后复用后端连接恢复", ("11.4.3.1",), "parse_error", topology="mmr", route_mode="hint"),
    _case("parse_error_multiple", "多 Parse 序列中间失败后恢复", ("11.4.3.2",), "parse_error", topology="mmr", route_mode="hint"),
    _case(
        "global_prepared_statements", "PreparedStatements 全局缓存", ("11.5.2", "11.5.3"),
        "global_ps", topology="mmr", route_mode="hint", notes=(
            "PreparedStatements功能是否正常，切换节点后能否正常使用。",
            "缓存信息是否正确：包括sql信息、sql分类信息，全局名称。",
            "基于全局缓存的读写切换功能是否正常。",
            "全局缓存复用，bypass响应是否正确。",
            "控制台显示的缓存信息，缓存统计信息是否正确。",
        ),
        step_mapping=(
            ("1", "前置条件", "启动独立 fbasecman 并确保全局缓存为空"),
            ("2", "前置条件", "在 mmr1 准备三条初始数据"),
            ("3", "前置条件", "在 mmr2 准备三条初始数据"),
            ("4", "1、2", "PreparedStatement DELETE/INSERT 与首次两条全局缓存"),
            ("5", "1、2、3", "默认查询结果、写主路由和四条缓存"),
            ("6", "1、2、3", "第一次 READ ONLY 的结果、读路由和 RW_HINT_READ"),
            ("7", "1、2、3", "第一次 READ WRITE 的结果、写主路由和 RW_HINT_WRITE"),
            ("8", "1、2、3", "第二次 READ ONLY 的结果、读路由和缓存复用"),
            ("9", "1、2、3", "第二次 READ WRITE 的结果、写主路由和缓存复用"),
            ("10", "1、2、3", "BEGIN READ ONLY 的结果、读路由和 BEGIN_READ_ONLY"),
            ("11", "2、4、5", "首次执行的七条缓存、bypass 与 ref_count"),
            ("12", "5", "首次执行的缓存统计"),
            ("13", "1、4", "第二次 PreparedStatement 初始化，不新增缓存"),
            ("14", "1、3、4", "第二次默认查询与缓存复用"),
            ("15", "1、3、4", "第二次 READ ONLY 查询与缓存复用"),
            ("16", "1、3、4", "第二次 READ WRITE 查询与缓存复用"),
            ("17", "1、3、4", "第二次 READ ONLY 查询与缓存复用"),
            ("18", "1、3、4", "第二次 READ WRITE 查询与缓存复用"),
            ("19", "1、3、4", "第二次 BEGIN READ ONLY 查询与缓存复用"),
            ("20", "4、5", "第二次执行后的 hits、misses 与缓存总数"),
            ("21", "1 至 5", "上述业务断言的汇总判定"),
        ),
    ),
    _case("global_prepared_special_sql", "全局缓存的心跳、GUC 和事务标签", ("11.5.3.2",), "global_ps_special", topology="mmr", route_mode="hint"),
    _case("global_prepared_eviction", "全局和后端 PreparedStatement 缓存容量淘汰", ("11.5.3.3.1",), "global_ps_eviction", topology="mmr", route_mode="hint"),
    _case("global_prepared_bypass_retention", "持有 bypass 响应的全局缓存条目不淘汰", ("11.5.3.3.2",), "global_ps_bypass", topology="mmr", route_mode="hint"),
]


def case_items(include_long_time=True):
    return [case for case in HANDOVER_CASES if case.enabled and (include_long_time or not case.long_time)]


def find_case(name):
    for case in HANDOVER_CASES:
        if case.name == name:
            return case
    raise KeyError(name)


def validate_manifest():
    seen = {}
    for case in HANDOVER_CASES:
        if not case.source_sections:
            raise ValueError("%s has no source section" % case.target)
        if not case.notes:
            raise ValueError("%s has no document test content" % case.target)
        if not case.step_mapping and not case.step_rules:
            raise ValueError("%s has no report-step document mapping" % case.target)
        for _, content, _ in case.step_mapping:
            _validate_content_reference(case, content)
        for expression, content, _ in case.step_rules:
            re.compile(expression)
            _validate_content_reference(case, content)
        for section in case.source_sections:
            if section in seen:
                raise ValueError("source section %s repeated by %s and %s" % (section, seen[section], case.target))
            seen[section] = case.target
    return seen


def _validate_content_reference(case, content):
    """Reject a mapping that points outside this case's rendered contents."""
    if content in ("前置条件", "环境恢复"):
        return
    numbers = [int(value) for value in re.findall(r"\d+", str(content))]
    if not numbers or any(value < 1 or value > len(case.notes) for value in numbers):
        raise ValueError("%s has invalid document-content reference %r" % (case.target, content))
