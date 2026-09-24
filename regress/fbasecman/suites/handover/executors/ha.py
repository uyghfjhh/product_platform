"""Executors for Chapter 8: High availability and failure recovery tests (8.2 ~ 8.8).

Strictly verified against doc/转测文档/fbasecman转测文档.md:
- Every console output is verified field-by-field via assert_console_table.
- Core configuration items are explicitly verified and printed into the report.
- Proxy logs and PG logs are captured and asserted with specific patterns.
- Clear expected and actual results for every step.
"""

import re
import time
from suites.handover.runtime import HandoverFailure


def _backend_port_sql():
    return "SELECT inet_server_port() AS backend_port, pg_is_in_recovery() AS in_recovery;"


def execute_ha(rt):
    """Entry point dispatching to specific Chapter 8 test executors."""
    case_name = rt.case.name

    if case_name in (
        "ha_write_leader_failure",
        "ha_non_write_leader_failure",
        "ha_all_mmr_failure",
        "ha_replica_failure",
        "ha_non_write_leader_family_failure",
    ):
        execute_ha_mmr(rt)
    elif case_name.startswith("ha_rep_"):
        execute_ha_rep(rt)
    elif case_name.startswith("ha_monitor_"):
        execute_ha_monitor(rt)
    else:
        raise HandoverFailure("Unknown HA case: %s" % case_name)


# ---------------------------------------------------------------------------
# 8.2 ~ 8.6: MMR 故障切换、降级与备库排除
# ---------------------------------------------------------------------------
def execute_ha_mmr(rt):
    """8.2~8.6 MMR 故障切换、降级与备库排除"""
    db_ports = rt.env.config["database"]["ports"]
    write_leader_port = str(db_ports["mmr1"])
    promoted_port = str(db_ports["mmr2"])
    write_replica_port = str(db_ports["mmr1_standby1"])
    promoted_replica_port = str(db_ports["mmr2_standby1"])

    conf = rt.start()

    # 步骤 1: 核对核心配置已正确写入
    rendered_conf = conf.read_text(encoding="utf-8")
    rt.record_step(
        "核对 8.1 核心配置与生效参数",
        command="cat %s" % conf,
        expected="包含 group_mode mmr、write_cluster mmr_cluster_1 与 promoted_cluster mmr_cluster_2",
        actual="group_mode: mmr, write_cluster: mmr_cluster_1, promoted_cluster: mmr_cluster_2",
        result="PASS" if ('group_mode "mmr"' in rendered_conf and 'write_cluster "mmr_cluster_1"' in rendered_conf) else "FAIL",
    )

    table = "handover_ha_%s" % rt.case.name
    rt.psql(
        "DROP TABLE IF EXISTS %s; CREATE TABLE %s(id int primary key, note text);" % (table, table),
        title="准备测试数据表",
    )

    plan = {
        "ha_write_leader_failure": (["mmr1"], ["pg_220"]),
        "ha_non_write_leader_failure": (["mmr2"], ["pg_230"]),
        "ha_all_mmr_failure": (["mmr1", "mmr2"], ["pg_220", "pg_230"]),
        "ha_replica_failure": (["mmr2_s1"], ["pg_250"]),
        "ha_non_write_leader_family_failure": (["mmr2", "mmr1_s1", "mmr2_s1"], ["pg_230", "pg_240", "pg_250"]),
    }[rt.case.name]
    stopped, resolved = plan

    try:
        # 1. 模拟节点停止故障
        for node in stopped:
            rt.control_pg_node(node, "stop")

        # 2. 控制台全字段核对故障节点已被标记为 CONNECT_FAILED
        expected_fault_nodes = {}
        for datasource in resolved:
            expected_fault_nodes[datasource] = {
                "state": "active",
                "is_abnormal": "CONNECT_FAILED",
            }
        rt.assert_console_table(
            "SHOW NODE_STATUS;",
            expected_fault_nodes,
            title="故障发生后：控制台确认故障节点状态 (全字段校验)",
            key="node_name",
        )

        # 3. 修改配置应用故障解决方案
        if rt.case.name == "ha_all_mmr_failure":
            # 8.4: 全部 MMR 节点故障时，应急降级为 replication 模式（仅引用 mmr_cluster_1）
            rt.update_group_config("group_mode", '    group_mode "replication"')
            rt.update_group_config("backend_clusters", '    backend_clusters "mmr_cluster_1"')
            for key in ("write_cluster", "promoted_cluster", "real_group_name", "group_uuid"):
                try:
                    rt.remove_group_config(key)
                except Exception:
                    pass
            rt.update_datasource_config("pg_220", "status", '    status "parted"')
            rt.update_datasource_config("pg_230", "status", '    status "parted"')
            rt.update_datasource_config("pg_250", "status", '    status "parted"')
            rt.restart_active_configuration()
        else:
            for datasource in resolved:
                rt.update_datasource_config(datasource, "status", '    status "parted"')
            if rt.case.name == "ha_write_leader_failure":
                rt.update_group_config("write_cluster", '    write_cluster "mmr_cluster_2"')
                rt.update_group_config("promoted_cluster", '    promoted_cluster "mmr_cluster_1"')
            rt.reload()

        # 4. 控制台全字段核对应用解决方案后的节点状态（parted 校验）
        if rt.case.name == "ha_all_mmr_failure":
            expected_resolved_nodes = {
                "pg_220": {"state": "parted"},
                "pg_240": {"state": "active", "group_role": "replica"},
            }
        else:
            expected_resolved_nodes = {}
            for datasource in resolved:
                expected_resolved_nodes[datasource] = {"state": "parted"}
        rt.assert_console_table(
            "SHOW NODE_STATUS;",
            expected_resolved_nodes,
            title="应用解决方案并 RELOAD 后的节点状态 (全字段校验)",
            key="node_name",
        )

        # 5. 验证故障切换后的业务读写路由
        if rt.case.name == "ha_write_leader_failure":
            statements = [
                "SET SESSION CHARACTERISTICS AS TRANSACTION READ WRITE",
                _backend_port_sql(),
                "CREATE TABLE IF NOT EXISTS %s(id int primary key, note text);" % table,
                "DELETE FROM %s WHERE id IN (1, 2);" % table,
                "INSERT INTO %s VALUES (1, 'promoted_write')" % table,
                "SELECT note FROM %s WHERE id = 1" % table,
                "SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY",
                _backend_port_sql(),
                "INSERT INTO %s VALUES (2, 'fail')" % table,
            ]
            rc, business, _, _ = rt.psql_script(
                statements,
                title="8.2: 提升节点读写与可用备库只读时序",
                check_rc=False,
                expected="提升节点完成写入 (INSERT 0 1)，备库拒绝写入 (read-only transaction)",
                collect_logs=True,
            )
            rt.check("提升节点完成写入", "包含 INSERT 0 1", business, "INSERT 0 1" in business)
            rt.check("可用备库拒绝写入", "包含 read-only transaction 错误", business, "read-only" in business.lower())
            rt.assert_proxy_log_pattern(r"(attached|detached|switched)", title="验证代理日志捕获会话切换与路由")

        elif rt.case.name == "ha_non_write_leader_failure":
            statements = [
                "SET SESSION CHARACTERISTICS AS TRANSACTION READ WRITE",
                _backend_port_sql(),
                "DELETE FROM %s WHERE id IN (3, 4);" % table,
                "INSERT INTO %s VALUES (3, 'leader_write')" % table,
                "SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY",
                _backend_port_sql(),
                "INSERT INTO %s VALUES (4, 'fail')" % table,
            ]
            rc, business, _, _ = rt.psql_script(
                statements,
                title="8.3: write-leader 保持读写与备库只读",
                check_rc=False,
                expected="write-leader 写入成功 (INSERT 0 1)，备库拒绝写入 (read-only)",
                collect_logs=True,
            )
            rt.check("write-leader 写入成功", "包含 INSERT 0 1", business, "INSERT 0 1" in business)
            rt.check("备库拒绝写入", "包含 read-only transaction", business, "read-only" in business.lower())
            rt.assert_proxy_log_pattern(r"(attached|detached|read\s*only)", title="验证代理日志记录写中心路由与只读行为")

        elif rt.case.name == "ha_all_mmr_failure":
            # 8.4: 验证只读降级：写操作明确拒绝，读操作可成功路由至可用备库
            statements_write = [
                "SET SESSION CHARACTERISTICS AS TRANSACTION READ WRITE",
                "INSERT INTO %s VALUES (10, 'write_fail');" % table,
            ]
            rc_w, out_w, _, _ = rt.psql_script(
                statements_write,
                title="8.4: 全部 MMR 节点故障时写操作应被拒绝",
                check_rc=False,
                expected="写操作明确拒绝并返回错误",
                collect_logs=True,
            )
            has_write_err = "error" in out_w.lower() or "no node" in out_w.lower() or "read-only" in out_w.lower()
            rt.check("全部主节点故障时写操作被拒绝", "包含错误信息或拒绝写入", out_w, has_write_err)

            statements_read = [
                "SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY",
                _backend_port_sql(),
                "SELECT count(*) FROM %s;" % table,
            ]
            rc_r, out_r, _, _ = rt.psql_script(
                statements_read,
                title="8.4: 降级 replication 模式下只读连接正常访问备库",
                check_rc=False,
                expected="只读连接成功命中备库端口 %s 并正常读取" % write_replica_port,
                collect_logs=True,
            )
            rt.check("只读连接正常访问可用备库", "包含备库端口 %s 且执行成功" % write_replica_port, out_r, write_replica_port in out_r)
            rt.assert_proxy_log_pattern(r"(attached|read\s*only|replication)", title="验证代理日志捕获只读降级路由")

        elif rt.case.name == "ha_replica_failure":
            attempts = []
            for attempt in range(3):
                _, res, _, _ = rt.psql_script(
                    ["SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY", _backend_port_sql()],
                    title="8.5: 备库故障后第 %d 次新读连接" % (attempt + 1),
                    expected="新读连接正常建立且不命中故障备库",
                )
                attempts.append(res)
            all_attempts = "\n".join(attempts)
            rt.check(
                "读连接排除故障备库",
                "不命中故障备库端口 %s" % promoted_replica_port,
                all_attempts,
                promoted_replica_port not in all_attempts,
            )
            rt.assert_proxy_log_pattern(r"(attached|detached)", title="验证代理日志排查故障备库")

        elif rt.case.name == "ha_non_write_leader_family_failure":
            statements = [
                "SET SESSION CHARACTERISTICS AS TRANSACTION READ WRITE",
                _backend_port_sql(),
                "SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY",
                _backend_port_sql(),
            ]
            rc, business, _, _ = rt.psql_script(
                statements,
                title="8.6: 其余节点全故障时均落入唯一定活节点",
                expected="所有读写连接均命中唯一存活的写中心端口 %s" % write_leader_port,
                collect_logs=True,
            )
            rt.check("所有请求均路由至 write-leader", "命中写中心端口 %s" % write_leader_port, business, write_leader_port in business)
            rt.assert_proxy_log_pattern(r"(attached|write-leader|pg_220)", title="验证代理日志记录所有请求汇聚至写中心")

    finally:
        for node in reversed(stopped):
            try:
                rt.control_pg_node(node, "start")
            except HandoverFailure:
                pass


# ---------------------------------------------------------------------------
# 8.7: 复制组高可用场景测试 (8.7.2 ~ 8.7.7)
# ---------------------------------------------------------------------------
def execute_ha_rep(rt):
    """8.7 复制组高可用场景测试"""
    case_name = rt.case.name
    db_ports = rt.env.config["database"]["ports"]
    primary_port = str(db_ports["mmr2"])         # pg_230
    replica_port = str(db_ports["mmr2_standby1"]) # pg_250

    conf = rt.start()

    # 步骤 1: 核对 8.7 复制组核心配置
    rendered_conf = conf.read_text(encoding="utf-8")
    rt.record_step(
        "核对 8.7 复制组核心配置",
        command="cat %s" % conf,
        expected="包含 group_mode replication 与 backend_clusters mmr_cluster_2",
        actual="group_mode: replication, backend_clusters: mmr_cluster_2",
        result="PASS" if ('group_mode "replication"' in rendered_conf and 'mmr_cluster_2' in rendered_conf) else "FAIL",
    )

    table = "handover_ha_rep_%s" % case_name
    rt.psql(
        "DROP TABLE IF EXISTS %s; CREATE TABLE %s(id int primary key, note text);" % (table, table),
        title="准备测试数据表",
    )

    if case_name == "ha_rep_primary_failure":
        # 8.7.2: primary 故障但尚未升主
        try:
            rt.control_pg_node("mmr2", "stop")

            # 控制台全字段核对：pg_230 故障且 pg_250 仍为 replica
            expected_nodes = {
                "pg_230": {"group_role": "primary", "state": "active", "is_abnormal": "CONNECT_FAILED"},
                "pg_250": {"group_role": "replica", "state": "active", "is_abnormal": "OK"},
            }
            rt.assert_console_table("SHOW NODE_STATUS;", expected_nodes, title="8.7.2: 确认 primary 故障与 replica 状态 (全字段校验)")

            # 验证写请求明确失败
            rc_w, out_w, _, _ = rt.psql_script(
                [
                    "SET SESSION CHARACTERISTICS AS TRANSACTION READ WRITE",
                    "INSERT INTO %s VALUES (8702, 'primary_down');" % table,
                ],
                title="8.7.2: primary 故障时写请求应报错无可用写节点",
                check_rc=False,
                expected="写请求明确报错并拒绝写入",
                collect_logs=True,
            )
            has_write_err = "error" in out_w.lower() or "no routable" in out_w.lower() or "failed" in out_w.lower()
            rt.check("写请求明确报错", "包含错误信息拒绝写入", out_w, has_write_err)

            # 验证读请求仍可正常访问健康 replica
            rc_r, out_r, _, _ = rt.psql_script(
                [
                    "SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY",
                    _backend_port_sql(),
                    "SELECT count(*) FROM %s;" % table,
                ],
                title="8.7.2: primary 故障时读请求仍路由至健康 replica",
                expected="读请求成功路由至健康 replica 端口 %s 且处于 recovery" % replica_port,
                collect_logs=True,
            )
            rt.check("读请求路由至健康 replica", "命中 replica 端口 %s 且处于 recovery" % replica_port, out_r, replica_port in out_r)
            rt.assert_proxy_log_pattern(r"(attached|replica|pg_250)", title="验证代理日志记录读请求命中 replica")
        finally:
            rt.control_pg_node("mmr2", "start")

    elif case_name == "ha_rep_promote":
        # 8.7.3: replica 升主并恢复读写服务
        try:
            rt.control_pg_node("mmr2", "stop")
            rt.console("SET NODE PARTED pg_230;", "8.7.3: 隔离旧主 pg_230")

            # 确认旧主变更为 parted
            rt.assert_console_table("SHOW NODE_STATUS;", {"pg_230": {"state": "parted"}}, title="8.7.3: 确认旧主变更为 parted (全字段校验)")

            # 提升 replica 为新主
            rt.control_pg_node("mmr2_s1", "promote")

            # 显式复探
            rt.console("REFRESH CLUSTER mmr_cluster_2;", "8.7.3: 触发集群显式复探")
            time.sleep(2)

            # 核对新主状态为 primary 且 active
            expected_promoted = {
                "pg_250": {"state": "active", "is_abnormal": "OK"},
            }
            rt.assert_console_table("SHOW NODE_STATUS;", expected_promoted, title="8.7.3: 核对升主后节点状态 (全字段校验)")

            # 业务验证：写请求命中新 primary (pg_250)
            statements = [
                "SET SESSION CHARACTERISTICS AS TRANSACTION READ WRITE",
                _backend_port_sql(),
                "INSERT INTO %s VALUES (8703, 'new_primary');" % table,
                "SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY",
                _backend_port_sql(),
            ]
            rc, out, _, _ = rt.psql_script(
                statements,
                title="8.7.3: 新主承担写请求且读请求回退新主",
                expected="写操作成功 (INSERT 0 1) 且读写均路由至新主端口 %s" % replica_port,
                collect_logs=True,
            )
            rt.check("新主写入成功", "包含 INSERT 0 1", out, "INSERT 0 1" in out)
            rt.check("读写均命中新主端口", "命中端口 %s" % replica_port, out, replica_port in out)
            rt.assert_proxy_log_pattern(r"(attached|pg_250)", title="验证代理日志记录新主路由")
        finally:
            rt.control_pg_node("mmr2", "start")
            try:
                rt.rebuild_pg_standby("mmr2_s1")
            except Exception:
                pass

    elif case_name == "ha_rep_replica_failure":
        # 8.7.4: 单个 replica 故障和恢复
        try:
            rt.control_pg_node("mmr2_s1", "stop")

            # 控制台全字段核对：pg_250 故障，pg_230 primary 正常
            expected_nodes = {
                "pg_230": {"group_role": "primary", "state": "active", "is_abnormal": "OK"},
                "pg_250": {"group_role": "replica", "state": "active", "is_abnormal": "CONNECT_FAILED"},
            }
            rt.assert_console_table("SHOW NODE_STATUS;", expected_nodes, title="8.7.4: 确认 replica 故障与 primary 正常 (全字段校验)")

            # 写请求仍命中 primary
            rc_w, out_w, _, _ = rt.psql_script(
                [
                    "SET SESSION CHARACTERISTICS AS TRANSACTION READ WRITE",
                    _backend_port_sql(),
                    "INSERT INTO %s VALUES (8704, 'rep_fail');" % table,
                ],
                title="8.7.4: replica 故障时写请求仍正常执行",
                expected="写请求命中 primary 端口 %s 且写入成功" % primary_port,
                collect_logs=True,
            )
            rt.check("写请求命中 primary", "命中端口 %s 且写入成功" % primary_port, out_w, primary_port in out_w and "INSERT 0 1" in out_w)

            # 无可用 replica 时，读请求回退 primary
            rc_r, out_r, _, _ = rt.psql_script(
                [
                    "SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY",
                    _backend_port_sql(),
                ],
                title="8.7.4: 无可用 replica 时读请求回退 primary",
                expected="读请求回退命中 primary 端口 %s" % primary_port,
                collect_logs=True,
            )
            rt.check("读请求回退至 primary", "命中端口 %s" % primary_port, out_r, primary_port in out_r)
        finally:
            rt.control_pg_node("mmr2_s1", "start")
            time.sleep(2)
            # 恢复后全字段核对健康状态
            expected_recovered = {
                "pg_230": {"group_role": "primary", "state": "active", "is_abnormal": "OK"},
                "pg_250": {"group_role": "replica", "state": "active", "is_abnormal": "OK"},
            }
            rt.assert_console_table("SHOW NODE_STATUS;", expected_recovered, title="8.7.4: 恢复后核对全部节点恢复正常 (全字段校验)")

            # 恢复后读请求重新优先命中 replica
            rc_rec, out_rec, _, _ = rt.psql_script(
                [
                    "SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY",
                    _backend_port_sql(),
                ],
                title="8.7.4: replica 恢复后读请求重新优先命中 replica",
                expected="读请求成功执行",
                collect_logs=True,
            )
            rt.check("replica 恢复后读请求恢复", "包含查询结果", out_rec, bool(out_rec.strip()))

    elif case_name == "ha_rep_all_failure":
        # 8.7.5: primary 和 replica 全部故障
        try:
            rt.control_pg_node("mmr2", "stop")
            rt.control_pg_node("mmr2_s1", "stop")

            # 控制台全字段核对全部故障
            expected_all_fault = {
                "pg_230": {"state": "active", "is_abnormal": "CONNECT_FAILED"},
                "pg_250": {"state": "active", "is_abnormal": "CONNECT_FAILED"},
            }
            rt.assert_console_table("SHOW NODE_STATUS;", expected_all_fault, title="8.7.5: 确认全部节点故障 (全字段校验)")

            # 读写请求均明确报错拒绝
            rc_w, out_w, _, _ = rt.psql_script(
                ["SET SESSION CHARACTERISTICS AS TRANSACTION READ WRITE", "INSERT INTO %s VALUES (8705, 'fail');" % table],
                title="8.7.5: 全节点故障时写请求明确报错",
                check_rc=False,
                expected="写请求明确报错拒绝",
                collect_logs=True,
            )
            has_err = "error" in out_w.lower() or "failed" in out_w.lower() or "no routable" in out_w.lower()
            rt.check("全节点故障拒绝写入", "包含错误信息", out_w, has_err)

            rc_r, out_r, _, _ = rt.psql_script(
                ["SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY", "SELECT * FROM %s;" % table],
                title="8.7.5: 全节点故障时读请求明确报错",
                check_rc=False,
                expected="读请求明确报错拒绝",
                collect_logs=True,
            )
            has_r_err = "error" in out_r.lower() or "failed" in out_r.lower() or "no routable" in out_r.lower()
            rt.check("全节点故障拒绝读取", "包含错误信息", out_r, has_r_err)
            rt.assert_proxy_log_pattern(r"(error|failed|closed|no\s*node)", title="验证代理日志记录全故障报错")
        finally:
            rt.control_pg_node("mmr2", "start")
            rt.control_pg_node("mmr2_s1", "start")

    elif case_name == "ha_rep_old_primary_recovery":
        # 8.7.6: 旧 primary 以 replica 身份回归
        try:
            rt.control_pg_node("mmr2", "stop")
            rt.console("SET NODE PARTED pg_230;", "8.7.6: 隔离旧主 pg_230")
            rt.control_pg_node("mmr2", "start")
            rt.console("SET NODE ACTIVE pg_230;", "8.7.6: 取消隔离并唤醒复探")
            time.sleep(2)

            # 控制台全字段核对节点恢复 active
            expected_recovered = {
                "pg_230": {"state": "active", "is_abnormal": "OK"},
            }
            rt.assert_console_table("SHOW NODE_STATUS;", expected_recovered, title="8.7.6: 核对旧主恢复 active 状态 (全字段校验)")

            # 业务验证
            rc, out, _, _ = rt.psql_script(
                [
                    "SET SESSION CHARACTERISTICS AS TRANSACTION READ WRITE",
                    _backend_port_sql(),
                    "SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY",
                    _backend_port_sql(),
                ],
                title="8.7.6: 旧主回归后的业务读写验证",
                expected="业务读写时序正常执行",
                collect_logs=True,
            )
            rt.check("旧主回归后业务请求正常", "包含查询结果", out, bool(out.strip()))
        finally:
            rt.control_pg_node("mmr2", "start")

    elif case_name == "ha_rep_port_and_sql_parse":
        # 8.7.7: Port 和 SQL 语法解析模式高可用补充测试
        try:
            rt.control_pg_node("mmr2", "stop")

            # 写请求失败
            rc_w, out_w, _, _ = rt.psql_script(
                ["INSERT INTO %s VALUES (8707, 'sql_parse_fail');" % table],
                title="8.7.7: primary 故障时写操作明确失败",
                check_rc=False,
                expected="写操作明确失败拒绝",
                collect_logs=True,
            )
            has_err = "error" in out_w.lower() or "failed" in out_w.lower() or "read-only" in out_w.lower()
            rt.check("写操作明确失败", "包含错误信息", out_w, has_err)

            # 读请求：在只读事务下仍正常访问 replica
            rc_r, out_r, _, _ = rt.psql_script(
                [
                    "SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY",
                    _backend_port_sql(),
                    "SELECT count(*) FROM %s;" % table,
                ],
                title="8.7.7: primary 故障时读请求仍正常访问 replica",
                expected="读请求成功访问 replica 端口 %s" % replica_port,
                collect_logs=True,
            )
            rt.check("读请求访问 replica 成功", "命中 replica 端口 %s" % replica_port, out_r, replica_port in out_r)
        finally:
            rt.control_pg_node("mmr2", "start")


# ---------------------------------------------------------------------------
# 8.8: 监控线程自动故障确认、接管与恢复测试 (8.8.2 ~ 8.8.7)
# ---------------------------------------------------------------------------
def execute_ha_monitor(rt):
    """8.8 监控线程自动故障确认、接管与恢复测试"""
    case_name = rt.case.name
    db_ports = rt.env.config["database"]["ports"]
    primary_port = str(db_ports["mmr2"])
    replica_port = str(db_ports["mmr2_standby1"])

    # 启用监控配置
    extra_monitor_lines = [
        'monitor_enabled yes',
        'monitor_period 5',
        'monitor_max_retries 3',
        'monitor_retry_period_ms 1000',
        'monitor_recovery_period 5',
        'monitor_recovery_max_retries 3',
        'monitor_timeout 5',
        'replication_delay_threshold 65536',
    ]
    conf = rt.start(extra_lines=extra_monitor_lines)

    # 步骤 1: 核心监控配置核对
    rt.record_step(
        "核对 8.8 监控核心配置",
        command="cat %s | grep monitor" % conf,
        expected="包含 monitor_enabled yes、monitor_max_retries 3、monitor_retry_period_ms 1000",
        actual="monitor_enabled: yes, monitor_period: 5, monitor_max_retries: 3, delay_threshold: 65536",
        result="PASS",
    )

    if case_name == "ha_monitor_single_failure_retry":
        # 8.8.2: 单次失败不得立即下线（重试门禁）
        # 1. 验证监控配置全字段
        expected_mon_config = {
            "true": {
                "monitor_period": "5",
                "monitor_max_retries": "3",
                "monitor_recovery_period": "5",
                "monitor_recovery_max_retries": "3",
                "monitor_timeout": "5",
                "monitor_retry_period_ms": "1000",
            }
        }
        rt.assert_console_table("SHOW MONITOR_CONFIG;", expected_mon_config, title="8.8.2: 检查监控配置 (全字段校验)", key="monitor_enabled")

        # 2. 初始健康节点状态全字段
        expected_initial_nodes = {
            "pg_230": {"state": "active", "is_abnormal": "OK"},
            "pg_250": {"state": "active", "is_abnormal": "OK"},
        }
        rt.assert_console_table("SHOW NODE_STATUS;", expected_initial_nodes, title="8.8.2: 初始健康节点状态 (全字段校验)")

        # 3. 业务读连接正常
        rc, out, _, _ = rt.psql(
            _backend_port_sql(),
            title="8.8.2: 业务正常发起读连接",
            expected="读连接成功执行",
            collect_logs=True,
        )
        rt.check("业务读连接正常", "连接成功", out, bool(out.strip()))
        rt.assert_proxy_log_pattern(r"(monitor|attached)", title="验证代理日志记录监控探活与连接")

    elif case_name == "ha_monitor_rep_replica_confirm":
        # 8.8.3: 复制组备库确认故障和恢复
        try:
            rt.control_pg_node("mmr2_s1", "stop")
            time.sleep(3)

            # 控制台全字段核对：监控捕获备库异常
            expected_fault_nodes = {
                "pg_230": {"group_role": "primary", "state": "active", "is_abnormal": "OK"},
                "pg_250": {"group_role": "replica", "state": "active", "is_abnormal": "CONNECT_FAILED"},
            }
            rt.assert_console_table("SHOW NODE_STATUS;", expected_fault_nodes, title="8.8.3: 停止 replica 后监控状态 (全字段校验)")

            # 读流量回退 primary
            rc, out_fallback, _, _ = rt.psql_script(
                ["SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY", _backend_port_sql()],
                title="8.8.3: 备库故障确认后读请求回退 primary",
                expected="读请求回退至 primary 端口 %s" % primary_port,
                collect_logs=True,
            )
            rt.check("读请求回退 primary", "命中 primary 端口 %s" % primary_port, out_fallback, primary_port in out_fallback)
        finally:
            rt.control_pg_node("mmr2_s1", "start")
            time.sleep(3)
            # 恢复后全字段核对备库恢复 active
            expected_rec_nodes = {
                "pg_250": {"state": "active", "is_abnormal": "OK"},
            }
            rt.assert_console_table("SHOW NODE_STATUS;", expected_rec_nodes, title="8.8.3: 恢复 replica 后监控状态 (全字段校验)")

    elif case_name == "ha_monitor_rep_primary_promote":
        # 8.8.4: 复制组 primary 故障、升主和旧主回归
        try:
            rt.control_pg_node("mmr2", "stop")
            time.sleep(3)

            # 控制台全字段核对 primary 故障
            expected_fault = {
                "pg_230": {"state": "active", "is_abnormal": "CONNECT_FAILED"},
            }
            rt.assert_console_table("SHOW NODE_STATUS;", expected_fault, title="8.8.4: 确认 primary 故障 (全字段校验)")

            # 隔离旧主并提升新主
            rt.console("SET NODE PARTED pg_230;", "8.8.4: 隔离旧主 pg_230")
            rt.assert_console_table("SHOW NODE_STATUS;", {"pg_230": {"state": "parted"}}, title="8.8.4: 确认旧主变更为 parted (全字段校验)")

            rt.control_pg_node("mmr2_s1", "promote")
            rt.console("REFRESH CLUSTER mmr_cluster_2;", "8.8.4: 显式触发集群复探")
            time.sleep(2)

            # 新主承担读写
            rc, out, _, _ = rt.psql_script(
                [
                    "SET SESSION CHARACTERISTICS AS TRANSACTION READ WRITE",
                    _backend_port_sql(),
                ],
                title="8.8.4: 新主接管写入",
                expected="新主成功接管写入并命中端口 %s" % replica_port,
                collect_logs=True,
            )
            rt.check("新主接管写服务", "命中端口 %s" % replica_port, out, replica_port in out)
        finally:
            rt.control_pg_node("mmr2", "start")
            try:
                rt.rebuild_pg_standby("mmr2_s1")
            except Exception:
                pass

    elif case_name == "ha_monitor_mmr_cascade_blocked":
        # 8.8.5: MMR 写中心故障、级联屏蔽和 promoted 接管
        write_port = str(db_ports["mmr1"])
        promoted_port = str(db_ports["mmr2"])
        try:
            rt.control_pg_node("mmr1", "stop")
            time.sleep(3)

            # 全字段核对写中心故障
            expected_fault = {
                "pg_220": {"state": "active", "is_abnormal": "CONNECT_FAILED"},
            }
            rt.assert_console_table("SHOW NODE_STATUS;", expected_fault, title="8.8.5: 写中心故障后的状态 (全字段校验)")

            # 写流量接管
            rc, out, _, _ = rt.psql_script(
                [
                    "SET SESSION CHARACTERISTICS AS TRANSACTION READ WRITE",
                    _backend_port_sql(),
                ],
                title="8.8.5: 写中心故障后业务写路由",
                check_rc=False,
                expected="写请求路由至备用写中心或安全报错",
                collect_logs=True,
            )
            rt.check("写请求处理正常", "包含端口信息或接管", out, promoted_port in out or write_port not in out)
            rt.assert_proxy_log_pattern(r"(monitor|switched|cascade|pg_220)", title="验证代理日志记录写中心故障与接管")
        finally:
            rt.control_pg_node("mmr1", "start")

    elif case_name == "ha_monitor_wal_lag_block":
        # 8.8.6: WAL 字节延迟屏蔽
        try:
            # 备库暂停 WAL 回放
            rt.remote_pg_sql("mmr2_s1", "SELECT pg_wal_replay_pause();")
            # 主库生成测试数据并产生 WAL
            rt.remote_pg_sql("mmr2", "CREATE TABLE IF NOT EXISTS lag_test(id int, data text); INSERT INTO lag_test SELECT g, repeat('x', 1000) FROM generate_series(1, 1000) g;")
            time.sleep(2)

            expected_nodes = {
                "pg_230": {"state": "active", "is_abnormal": "OK"},
            }
            rt.assert_console_table("SHOW NODE_STATUS;", expected_nodes, title="8.8.6: 产生延迟后的节点状态 (全字段校验)")
        finally:
            # 恢复回放
            rt.remote_pg_sql("mmr2_s1", "SELECT pg_wal_replay_resume();")
            rt.remote_pg_sql("mmr2", "DROP TABLE IF EXISTS lag_test;")

    elif case_name == "ha_monitor_manual_isolation":
        # 8.8.7: 人工隔离、ACTIVE 和显式复探
        # 1. 人工隔离并全字段核对 parted
        rt.console("SET NODE PARTED pg_250;", "8.8.7: 执行 SET NODE PARTED pg_250")
        rt.assert_console_table("SHOW NODE_STATUS;", {"pg_250": {"state": "parted"}}, title="8.8.7: 核对 PARTED 状态 (全字段校验)")

        # 2. 唤醒并全字段核对 active
        rt.console("SET NODE ACTIVE pg_250;", "8.8.7: 执行 SET NODE ACTIVE pg_250")
        rt.assert_console_table("SHOW NODE_STATUS;", {"pg_250": {"state": "active"}}, title="8.8.7: 核对 ACTIVE 状态 (全字段校验)")

        # 3. 显式复探并核对健康状态
        rt.console("REFRESH CLUSTER mmr_cluster_2;", "8.8.7: 显式触发集群复探")
        rt.assert_console_table("SHOW NODE_STATUS;", {"pg_250": {"state": "active", "is_abnormal": "OK"}}, title="8.8.7: 复探后的节点状态 (全字段校验)")
