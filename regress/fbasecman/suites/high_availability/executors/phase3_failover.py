"""Phase 3 executors: Failover, promotion, and MMR write center drift (CORE-16, CORE-17)."""

import time
from framework.reporting import ReportCheck
from ..console_parser import ConsoleAssertionError


def run_core_16_rep_failover(rt):
    """CORE-16: Replication failover, old primary PARTED, standby promote, and rebuild."""
    ports = rt.env.config["database"]["ports"]
    a0_port = str(ports["mmr1"])
    a1_port = str(ports["mmr1_standby1"])
    rt.coverage_items = [
        "旧主库隔离：SET NODE PARTED test_mmr1 后从路由彻底剔除，成员状态置为 parted",
        "新主库升主与识别：pg_ctl promote A1 后识别为新主，is_write_target=true，物理写流量命中 %s 端口" % a1_port,
        "旧主重建为从库并回归：pg_basebackup 重建后 SET NODE ACTIVE，重新入围只读候选列表",
    ]
    rt.coverage_mapping = [
        ("1", "1", "旧主库隔离与剔除"),
        ("2", "2", "新主库升主与识别"),
        ("3", "3", "旧主重建为从库并回归"),
    ]
    rt.overview_steps = [
        "停止旧主 A0 并执行 SET NODE PARTED 隔离",
        "对备库 A1 执行 promote 升主，验证写路由切换与物理落点",
        "使用 pg_basebackup 将 A0 重建为从库并重新激活回归",
    ]

    rt.start()
    rt.nodes.ensure_all_running()

    # Step 1: Stop A0 and set parted
    rt.mark_time("T1_stop_old_primary")
    rt.nodes.stop_node("A0", immediate=True)
    stop_transcript = rt.nodes.last_operation_transcript

    set_parted = rt.admin_psql("SET NODE PARTED test_mmr1;", title="隔离旧主库 A0")
    snap_part = rt.admin_psql("SHOW GROUP_ROUTING qa_rep;", title="检查隔离后路由剔除")
    snap_part.assert_candidate_absent("test_mmr1")
    snap_mem = rt.admin_psql("SHOW GROUP_MEMBERS;", title="检查隔离后成员状态")
    snap_mem.assert_field({"node_name": "test_mmr1", "group_name": "qa_rep"}, "state", "parted")

    rt.add_step(
        title="旧主库隔离与剔除",
        coverage="1",
        coverage_check="验证旧主库停机后执行 PARTED 隔离生效",
        action="通过 SSH 停止远程旧主库 A0 (%s:%s)，控制台执行 SET NODE PARTED test_mmr1; 隔离" % (rt.nodes.host, a0_port),
        command="%s\n\n%s" % (stop_transcript, rt.console_result(set_parted)),
        intermediate="SHOW GROUP_ROUTING qa_rep:\n%s\n\nSHOW GROUP_MEMBERS:\n%s" % (
            snap_part.format_table(), snap_mem.format_table()
        ),
        evidence="\n".join(rt.extract_log_lines(["test_mmr1", "parted", "removed"], max_lines=4)),
        expected="A0 从 SHOW GROUP_ROUTING 彻底剔除，SHOW GROUP_MEMBERS 显示 state=parted",
        actual="A0 路由剔除生效，成员状态已置为 parted",
        result="PASS",
        checks=[
            ReportCheck(
                title="旧主已从路由候选剔除",
                expected="test_mmr1 不存在于 SHOW GROUP_ROUTING 候选列表中",
                actual="已确认路由表中无 test_mmr1",
                result="PASS",
            ),
            ReportCheck(
                title="旧主成员状态置为 parted",
                expected="node_name=test_mmr1, group_name=qa_rep, state=parted",
                actual=snap_mem.format_record(node_name="test_mmr1", group_name="qa_rep"),
                result="PASS",
            ),
        ],
    )

    # Step 2 & 3: Promote A1 and verify
    rt.nodes.promote_node("A1")
    promote_transcript = rt.nodes.last_operation_transcript
    time.sleep(2)
    refresh_promote = rt.admin_psql("REFRESH CLUSTER site_a;", title="刷新 site_a 拓扑")

    deadline = time.time() + 18
    snap_cluster = None
    while time.time() < deadline:
        snap_cluster = rt.admin_psql("SHOW CLUSTERS;")
        try:
            row = snap_cluster.find_one(cluster_name="site_a")
            if row.get("last_trusted_primary") == "test_mmr1_s1":
                break
        except Exception:
            pass
        time.sleep(1)
        rt.admin_psql("REFRESH CLUSTER site_a;")
    snap_cluster.assert_field({"cluster_name": "site_a"}, "last_trusted_primary", "test_mmr1_s1")

    snap_route = rt.admin_psql("SHOW GROUP_ROUTING qa_rep;")
    snap_route.assert_field({"candidate_node": "test_mmr1_s1"}, "is_write_target", "true")
    snap_route.assert_field({"candidate_node": "test_mmr1_s1"}, "candidate_type", "WRITE")
    snap_route.assert_field({"candidate_node": "test_mmr1_s1"}, "route_status", "AVAILABLE")
    rt.mark_time("T3_new_primary_confirmed")

    # Step 3.1: Verify write traffic hits new primary A1 (10012)
    rc_w, out_w = rt.client_psql("SELECT inet_server_port();", db="qa_rep")
    if rc_w != 0 or out_w != a1_port:
        raise ConsoleAssertionError("Expected write traffic to hit new primary A1 (%s), got %s" % (a1_port, out_w))

    rt.add_step(
        title="新主库升主与识别",
        coverage="2",
        coverage_check="验证新主库升主、路由识别与物理落点穿透",
        action="通过 SSH 执行 pg_ctl promote A1 (%s)，执行 REFRESH CLUSTER site_a 并直连代理端口发送写入流量验证实际落点" % a1_port,
        command="%s\n\n%s\n\n客户端写验证:\n%s\n返回码: %s\n输出: %s" % (
            promote_transcript, rt.console_result(refresh_promote), rt.last_client_cmd, rc_w, out_w),
        intermediate="SHOW CLUSTERS:\n%s\n\nSHOW GROUP_ROUTING qa_rep:\n%s" % (
            snap_cluster.format_table(), snap_route.format_table()
        ),
        evidence="\n".join(rt.extract_log_lines(["site_a", "test_mmr1_s1", "primary", "promote", "route"], max_lines=6)),
        expected="A1 识别为新主库，is_write_target=true，route_status=AVAILABLE，物理写请求穿透命中 %s 端口" % a1_port,
        actual="test_mmr1_s1 晋升为主库，写目标已切换，客户端写入真实落点为 %s" % a1_port,
        result="PASS",
        checks=[
            ReportCheck(
                title="动态识别新主库",
                expected="last_trusted_primary=test_mmr1_s1",
                actual=snap_cluster.format_record(cluster_name="site_a"),
                result="PASS",
            ),
            ReportCheck(
                title="写路由更新指向新主",
                expected="candidate_node=test_mmr1_s1, candidate_type=WRITE, is_write_target=true, route_status=AVAILABLE",
                actual=snap_route.format_record(candidate_node="test_mmr1_s1"),
                result="PASS",
            ),
            ReportCheck(
                title="物理写流量穿透落点验证",
                expected="SELECT inet_server_port(); 返回 %s" % a1_port,
                actual="实际连接端口=%s" % out_w,
                result="PASS",
            ),
        ],
    )

    # Step 4: Rebuild A0 as replica of A1
    rt.nodes.rebuild_replica("A0", "A1")
    rebuild_transcript = rt.nodes.last_operation_transcript
    set_active_a0 = rt.admin_psql("SET NODE ACTIVE test_mmr1;", title="重新激活 A0 作为从库")
    refresh_a0 = rt.admin_psql("REFRESH CLUSTER site_a;")

    deadline = time.time() + 18
    snap_rec = None
    while time.time() < deadline:
        snap_rec = rt.admin_psql("SHOW GROUP_ROUTING qa_rep;")
        rows = snap_rec.find_rows(candidate_node="test_mmr1", candidate_type="READ")
        if rows and rows[0].get("effective_state") == "active":
            break
        time.sleep(1)
        rt.admin_psql("REFRESH CLUSTER site_a;")
    snap_rec.assert_field({"candidate_node": "test_mmr1"}, "effective_state", "active")
    snap_rec.assert_field({"candidate_node": "test_mmr1"}, "candidate_type", "READ")
    snap_rec.assert_field({"candidate_node": "test_mmr1"}, "route_status", "AVAILABLE")
    rt.mark_time("T5_failover_rebuild_completed")

    rt.add_step(
        title="旧主重建为从库并回归",
        coverage="3",
        coverage_check="验证旧主拉平并重建为从库后恢复准入只读",
        action="使用 pg_basebackup 从新主 A1 重建 A0，控制台执行 SET NODE ACTIVE test_mmr1 并刷新拓扑",
        command="%s\n\n%s\n\n%s" % (
            rebuild_transcript, rt.console_result(set_active_a0), rt.console_result(refresh_a0)),
        intermediate=snap_rec.format_table(),
        evidence="\n".join(rt.extract_log_lines(["test_mmr1", "active", "replica", "AVAILABLE"], max_lines=6)),
        expected="A0 恢复为 active 读节点，candidate_type=READ，route_status=AVAILABLE",
        actual="A0 成功作为从库重新准入只读候选",
        result="PASS",
        checks=[
            ReportCheck(
                title="旧主重建从库回归",
                expected="candidate_node=test_mmr1, candidate_type=READ, effective_state=active, route_status=AVAILABLE",
                actual=snap_rec.format_record(candidate_node="test_mmr1"),
                result="PASS",
            )
        ],
    )

    # Cleanup: restore original baseline (A0 primary, A1 replica)
    cleanup_parted = rt.admin_psql("SET NODE PARTED test_mmr1_s1;")
    rt.nodes.stop_node("A1", immediate=True)
    stop_a1 = rt.nodes.last_operation_transcript
    rt.nodes.promote_node("A0")
    promote_a0 = rt.nodes.last_operation_transcript
    time.sleep(2)
    cleanup_refresh_1 = rt.admin_psql("REFRESH CLUSTER site_a;")
    rt.nodes.rebuild_replica("A1", "A0")
    rebuild_a1 = rt.nodes.last_operation_transcript
    cleanup_active = rt.admin_psql("SET NODE ACTIVE test_mmr1_s1;")
    cleanup_refresh_2 = rt.admin_psql("REFRESH CLUSTER site_a;")
    time.sleep(2)
    cleanup_clusters = rt.admin_psql("SHOW CLUSTERS;")
    cleanup_routes = rt.admin_psql("SHOW GROUP_ROUTING qa_rep;")
    cleanup_clusters.assert_field({"cluster_name": "site_a"}, "current_primary", "test_mmr1")
    cleanup_routes.assert_field({"candidate_node": "test_mmr1", "candidate_type": "WRITE"}, "route_status", "AVAILABLE")
    cleanup_routes.assert_field({"candidate_node": "test_mmr1_s1", "candidate_type": "READ"}, "route_status", "AVAILABLE")
    rt.add_step(
        title="恢复原始复制拓扑基线",
        action="将 A0 恢复为主库、A1 从 A0 重建为流复制备库，并恢复节点 ACTIVE",
        command="\n\n".join((rt.console_result(cleanup_parted), stop_a1, promote_a0,
                               rt.console_result(cleanup_refresh_1), rebuild_a1,
                               rt.console_result(cleanup_active), rt.console_result(cleanup_refresh_2))),
        intermediate="SHOW CLUSTERS:\n%s\n\nSHOW GROUP_ROUTING qa_rep:\n%s" % (
            cleanup_clusters.format_table(), cleanup_routes.format_table()),
        expected="site_a 主库恢复为 A0，A1 恢复为 AVAILABLE 只读备库",
        actual="current_primary=test_mmr1；A0 WRITE AVAILABLE；A1 READ AVAILABLE",
        result="PASS",
    )


def run_core_17_mmr_write_center_failover(rt):
    """CORE-17: MMR write center automatic failover to promoted cluster and manual switch."""
    ports = rt.env.config["database"]["ports"]
    a0_port = str(ports["mmr1"])
    b0_port = str(ports["mmr2"])
    rt.coverage_items = [
        "初始写中心验证：双向 MMR 组初始写中心为 site_a (A0 为唯一写目标，write_source=WRITE_CLUSTER)",
        "自动漂移至 promoted 中心：A0 故障后，写中心自动漂移至 B0 (write_source=PROMOTED_CLUSTER, fallback_reason=WRITE_CLUSTER_UNAVAILABLE)",
        "全局唯一写目标保证：全集群有且仅有 1 个写目标，物理写流量穿透至 %s 端口" % b0_port,
        "人工 SET NODE WRITE 锁定与组间隔离：人工指定写中心后 write_source=WRITE_CLUSTER，且其它组不受影响",
    ]
    rt.coverage_mapping = [
        ("1", "1", "MMR 初始写中心检查"),
        ("2", "2、3", "MMR 自动漂移至 promoted 中心"),
        ("3", "4", "人工 SET NODE WRITE 锁定与组隔离"),
    ]
    rt.overview_steps = [
        "检查 MMR 初始写中心为 A0",
        "停止 A0，验证自动漂移至 B0 及全局单写目标",
        "验证物理写流量穿透落点至 %s" % b0_port,
        "执行人工 SET NODE WRITE 锁定并验证组隔离",
    ]

    rt.start()
    rt.nodes.ensure_all_running()

    # Step 1: Initial baseline (A0 is write target, write_source=WRITE_CLUSTER)
    snap_init = rt.admin_psql("SHOW GROUP_ROUTING qa_mmr;")
    snap_init.assert_field({"candidate_node": "test_mmr1"}, "is_write_target", "true")
    snap_init.assert_field({"candidate_node": "test_mmr1"}, "write_source", "WRITE_CLUSTER")
    snap_init.assert_write_target_count(1, user_name="postgres")
    rt.add_step(
        title="MMR 初始写中心检查",
        coverage="1",
        coverage_check="验证双向多主组初始写中心配置",
        action="控制台执行 SHOW GROUP_ROUTING qa_mmr; 检查双向多主写中心",
        command=rt.console_result(snap_init),
        intermediate=snap_init.format_table(),
        expected="A0 (test_mmr1) 为唯一写中心，write_source=WRITE_CLUSTER",
        actual="A0 为唯一写中心，单用户下写目标数量唯一",
        result="PASS",
        checks=[
            ReportCheck(
                title="A0 为初始写中心",
                expected="candidate_node=test_mmr1, is_write_target=true, write_source=WRITE_CLUSTER",
                actual=snap_init.format_record(candidate_node="test_mmr1"),
                result="PASS",
            ),
            ReportCheck(
                title="写目标全局唯一",
                expected="有且仅有 1 个 is_write_target=true 的节点",
                actual="写目标节点数: 1",
                result="PASS",
            ),
        ],
    )

    # Step 2 & 3: Stop A0 and wait for automatic drift to B0
    rt.mark_time("T1_stop_write_center")
    rt.nodes.stop_node("A0", immediate=True)
    stop_a0_transcript = rt.nodes.last_operation_transcript

    deadline = time.time() + 18
    snap_drift = None
    while time.time() < deadline:
        snap_drift = rt.admin_psql("SHOW GROUP_ROUTING qa_mmr;")
        rows = snap_drift.find_rows(candidate_node="test_mmr2", is_write_target="true")
        if rows and rows[0].get("write_source") == "PROMOTED_CLUSTER":
            break
        time.sleep(1)

    snap_drift.assert_write_target_count(1, user_name="postgres")
    snap_drift.assert_field({"candidate_node": "test_mmr2"}, "is_write_target", "true")
    snap_drift.assert_field({"candidate_node": "test_mmr2"}, "write_source", "PROMOTED_CLUSTER")
    snap_drift.assert_field({"candidate_node": "test_mmr2"}, "fallback_reason", "WRITE_CLUSTER_UNAVAILABLE")
    snap_drift.assert_field({"candidate_node": "test_mmr2"}, "effective_state", "promoted")
    snap_drift.assert_field({"candidate_node": "test_mmr2"}, "effective_grouprole", "write-leader")

    # Verify write traffic on promoted center B0 (port 10021)
    rc_w, out_w = rt.client_psql("SELECT inet_server_port();", db="qa_mmr")
    if rc_w != 0 or out_w != b0_port:
        raise ConsoleAssertionError("Expected write traffic to hit B0 (%s), got %s" % (b0_port, out_w))
    rt.mark_time("T3_write_drift_published")

    rt.add_step(
        title="MMR 自动漂移至 promoted 中心",
        coverage="2、3",
        coverage_check="验证原写中心故障后自动漂移与全局单写目标",
        action="通过 SSH 停止 A0 (%s:%s)，等待路由自动漂移并通过客户端验证写入落点" % (rt.nodes.host, a0_port),
        command="%s\n\n客户端写验证:\n%s\n返回码: %s\n输出: %s" % (
            stop_a0_transcript, rt.last_client_cmd, rc_w, out_w),
        intermediate=snap_drift.format_table(),
        evidence="\n".join(rt.extract_log_lines(["qa_mmr", "drift", "promoted", "fallback", "test_mmr2"], max_lines=6)),
        expected="B0 成为唯一写中心，write_source=PROMOTED_CLUSTER，fallback_reason=WRITE_CLUSTER_UNAVAILABLE，物理落点 %s" % b0_port,
        actual="漂移成功，字段全部吻合且物理写入真实命中 %s" % b0_port,
        result="PASS",
        checks=[
            ReportCheck(
                title="自动漂移至 B0 接管写中心",
                expected="candidate_node=test_mmr2, is_write_target=true, write_source=PROMOTED_CLUSTER, fallback_reason=WRITE_CLUSTER_UNAVAILABLE",
                actual=snap_drift.format_record(candidate_node="test_mmr2"),
                result="PASS",
            ),
            ReportCheck(
                title="写目标全局唯一性",
                expected="有且仅有 1 个 is_write_target=true 节点",
                actual="写目标节点数: 1",
                result="PASS",
            ),
            ReportCheck(
                title="物理写流量穿透命中 B0",
                expected="SELECT inet_server_port(); 返回 %s" % b0_port,
                actual="实际连接端口=%s" % out_w,
                result="PASS",
            ),
        ],
    )

    # Step 4: Recover A0
    rt.nodes.start_node("A0")
    recover_a0 = rt.nodes.last_operation_transcript
    deadline = time.time() + 18
    while time.time() < deadline:
        snap_rec = rt.admin_psql("SHOW GROUP_ROUTING qa_rep;")
        if snap_rec.find_rows(candidate_node="test_mmr1"):
            break
        time.sleep(1)
        rt.admin_psql("REFRESH CLUSTER site_a;")

    # Step 5: Manual switch via SET NODE WRITE ... IN GROUP qa_mmr
    refresh_a0 = rt.admin_psql("REFRESH CLUSTER site_a;")
    set_write = rt.admin_psql("SET NODE WRITE test_mmr2 IN GROUP qa_mmr;", title="人工命令锁定写中心为 B0")
    snap_manual = rt.admin_psql("SHOW GROUP_ROUTING qa_mmr;")
    snap_manual.assert_field({"candidate_node": "test_mmr2"}, "is_write_target", "true")
    snap_manual.assert_field({"candidate_node": "test_mmr2"}, "write_source", "WRITE_CLUSTER")

    # Group isolation check: other groups untouched
    snap_rep = rt.admin_psql("SHOW GROUP_ROUTING qa_rep;")
    snap_rep.assert_field({"cluster_name": "site_a"}, "group_name", "qa_rep")

    rt.add_step(
        title="人工 SET NODE WRITE 锁定与组隔离",
        coverage="4",
        coverage_check="验证人工命令锁定写中心与组配置隔离",
        action="控制台执行 SET NODE WRITE test_mmr2 IN GROUP qa_mmr; 锁定写中心，并检查 qa_rep 组隔离",
        command="%s\n\n%s\n\n%s" % (recover_a0, rt.console_result(refresh_a0), rt.console_result(set_write)),
        intermediate="SHOW GROUP_ROUTING qa_mmr:\n%s\n\nSHOW GROUP_ROUTING qa_rep:\n%s" % (
            snap_manual.format_table(), snap_rep.format_table()
        ),
        expected="B0 变为 WRITE_CLUSTER 锁定写中心，其他组不受影响保持独立路由",
        actual="B0 锁定成功，write_source=WRITE_CLUSTER，qa_rep 组隔离有效",
        result="PASS",
        checks=[
            ReportCheck(
                title="人工锁定写中心生效",
                expected="candidate_node=test_mmr2, write_source=WRITE_CLUSTER, is_write_target=true",
                actual=snap_manual.format_record(candidate_node="test_mmr2"),
                result="PASS",
            ),
            ReportCheck(
                title="组间配置严格隔离",
                expected="qa_rep 组仍由 site_a 承接，不受 qa_mmr 设置影响",
                actual="qa_rep 组主集群保持 site_a 未受干扰",
                result="PASS",
            ),
        ],
    )

    # Cleanup: restore write center to A0
    restore_write = rt.admin_psql("SET NODE WRITE test_mmr1 IN GROUP qa_mmr;")
    restore_refresh = rt.admin_psql("REFRESH CLUSTER site_a;")
    restored = rt.admin_psql("SHOW GROUP_ROUTING qa_mmr;")
    restored.assert_field({"candidate_node": "test_mmr1"}, "is_write_target", "true")
    restored.assert_field({"candidate_node": "test_mmr1"}, "write_source", "WRITE_CLUSTER")
    rt.add_step(
        title="恢复 MMR 初始写中心",
        command="%s\n\n%s" % (rt.console_result(restore_write), rt.console_result(restore_refresh)),
        intermediate=restored.format_table(),
        expected="qa_mmr 写中心恢复为 A0，write_source=WRITE_CLUSTER",
        actual="test_mmr1 is_write_target=true, write_source=WRITE_CLUSTER",
        result="PASS",
    )
