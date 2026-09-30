"""Phase 3 executors: Failover, promotion, and MMR write center drift (CORE-16, CORE-17)."""

import time
from platform_regress.reporting import ReportCheck
from ..console_parser import ConsoleAssertionError
def run_core_16_rep_failover(context):
    """CORE-16: Replication failover, old primary PARTED, standby promote, and rebuild."""
    ops = context.ops
    ports = ops.env.config["database"]["ports"]
    a0_port = str(ports["mmr1"])
    a1_port = str(ports["mmr1_standby1"])
    ops.coverage_items = [
        "旧主库隔离：SET NODE PARTED test_mmr1 后从路由彻底剔除，成员状态置为 parted",
        "新主库升主与识别：pg_ctl promote A1 后识别为新主，is_write_target=true，物理写流量命中 %s 端口" % a1_port,
        "旧主重建为从库并回归：pg_basebackup 重建后 SET NODE ACTIVE，重新入围只读候选列表",
    ]
    ops.coverage_mapping = [
        ("1", "1", "旧主库隔离与剔除"),
        ("2", "2", "新主库升主与识别"),
        ("3", "3", "旧主重建为从库并回归"),
    ]
    ops.overview_steps = [
        "停止旧主 A0 并执行 SET NODE PARTED 隔离",
        "对备库 A1 执行 promote 升主，验证写路由切换与物理落点",
        "使用 pg_basebackup 将 A0 重建为从库并重新激活回归",
    ]

    ops.start()
    ops.nodes.ensure_all_running()

    # Step 1: Stop A0 and set parted
    ops.mark_time("T1_stop_old_primary")
    ops.nodes.stop_node("A0", immediate=True)
    stop_transcript = ops.nodes.last_operation_transcript

    set_parted = ops.admin_psql("SET NODE PARTED test_mmr1;", title="隔离旧主库 A0")
    snap_part = ops.admin_psql("SHOW GROUP_ROUTING qa_rep;", title="检查隔离后路由剔除")
    snap_part.assert_candidate_absent("test_mmr1")
    snap_mem = ops.admin_psql("SHOW GROUP_MEMBERS;", title="检查隔离后成员状态")
    snap_mem.assert_field({"node_name": "test_mmr1", "group_name": "qa_rep"}, "state", "parted")
    snap_mem.assert_fields({"node_name": "test_mmr1", "group_name": "qa_rep"},
                           config_status="parted", group_role="primary")

    ops.add_step(
        title="旧主库隔离与剔除",
        coverage="1",
        coverage_check="验证旧主库停机后执行 PARTED 隔离生效",
        action="通过 SSH 停止远程旧主库 A0 (%s:%s)，控制台执行 SET NODE PARTED test_mmr1; 隔离" % (ops.nodes.host, a0_port),
        command="%s\n\n%s" % (stop_transcript, ops.console_result(set_parted)),
        intermediate="SHOW GROUP_ROUTING qa_rep:\n%s\n\nSHOW GROUP_MEMBERS:\n%s" % (
            snap_part.format_table(), snap_mem.format_table()
        ),
        evidence="\n".join(ops.extract_log_lines(["test_mmr1", "parted", "removed"], max_lines=4)),
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
    ops.nodes.promote_node("A1")
    promote_transcript = ops.nodes.last_operation_transcript
    time.sleep(2)
    refresh_promote = ops.admin_psql("REFRESH CLUSTER site_a;", title="刷新 site_a 拓扑")

    snap_cluster = ops.console_wait(
        "SHOW CLUSTERS;",
        lambda s: s.find_one(cluster_name="site_a").get("last_trusted_primary") == "test_mmr1_s1",
        timeout=18, interval=1.0, tolerant=True,
        retry=lambda: ops.admin_psql("REFRESH CLUSTER site_a;"),
        describe="site_a last_trusted_primary promoted to test_mmr1_s1")

    snap_route = ops.admin_psql("SHOW GROUP_ROUTING qa_rep;")
    snap_route.assert_field({"candidate_node": "test_mmr1_s1"}, "is_write_target", "true")
    snap_route.assert_field({"candidate_node": "test_mmr1_s1"}, "candidate_type", "WRITE")
    snap_route.assert_field({"candidate_node": "test_mmr1_s1"}, "route_status", "AVAILABLE")
    snap_route.assert_fields({"candidate_node": "test_mmr1_s1", "user_name": "qa_hint_user"},
                             effective_grouprole="primary", candidate_type="WRITE",
                             is_write_target="true", route_status="AVAILABLE")
    ops.mark_time("T3_new_primary_confirmed")

    write_command, write_output, row_id, direct_output = ops.verify_write_insert(
        "qa_rep", int(a1_port)
    )

    ops.add_step(
        title="新主库升主与识别",
        coverage="2",
        coverage_check="验证新主库升主、路由识别与物理落点穿透",
        action="通过 SSH 执行 pg_ctl promote A1 (%s)，执行 REFRESH CLUSTER site_a 并直连代理端口发送写入流量验证实际落点" % a1_port,
        command="%s\n\n%s\n\n客户端 INSERT RETURNING:\n%s\n输出: %s\n直连 A1 核对 id=%d: %s" % (
            promote_transcript, ops.console_result(refresh_promote),
            write_command, write_output, row_id, direct_output),
        intermediate="SHOW CLUSTERS:\n%s\n\nSHOW GROUP_ROUTING qa_rep:\n%s" % (
            snap_cluster.format_table(), snap_route.format_table()
        ),
        evidence="\n".join(ops.extract_log_lines(["site_a", "test_mmr1_s1", "primary", "promote", "route"], max_lines=6)),
        expected="A1 识别为新主库，is_write_target=true，route_status=AVAILABLE，物理写请求穿透命中 %s 端口" % a1_port,
        actual="test_mmr1_s1 晋升为主库，INSERT RETURNING 端口为 %s，直连确认 id=%d" %
               (a1_port, row_id),
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
                expected="INSERT RETURNING 端口为 %s，直连可查到新行" % a1_port,
                actual="代理返回=%s；直连行=%s" % (write_output, direct_output),
                result="PASS",
            ),
        ],
    )

    # Step 4: Rebuild A0 as replica of A1
    ops.nodes.rebuild_replica("A0", "A1")
    rebuild_transcript = ops.nodes.last_operation_transcript
    set_active_a0 = ops.admin_psql("SET NODE ACTIVE test_mmr1;", title="重新激活 A0 作为从库")
    refresh_a0 = ops.admin_psql("REFRESH CLUSTER site_a;")

    def read_candidate_active(snap):
        rows = snap.find_rows(candidate_node="test_mmr1", candidate_type="READ")
        return rows and rows[0].get("effective_state") == "active"

    snap_rec = ops.console_wait(
        "SHOW GROUP_ROUTING qa_rep;", read_candidate_active,
        timeout=18, interval=1.0,
        retry=lambda: ops.admin_psql("REFRESH CLUSTER site_a;"),
        describe="test_mmr1 back as active READ candidate")
    snap_rec.assert_field({"candidate_node": "test_mmr1"}, "effective_state", "active")
    snap_rec.assert_field({"candidate_node": "test_mmr1"}, "candidate_type", "READ")
    snap_rec.assert_field({"candidate_node": "test_mmr1"}, "route_status", "AVAILABLE")
    snap_rec.assert_fields({"candidate_node": "test_mmr1", "user_name": "qa_hint_user"},
                           effective_grouprole="replica", candidate_type="READ",
                           route_status="AVAILABLE")
    recovered_row = ops._query_scalar(int(a0_port),
                                     "SELECT id FROM qa_case.orders WHERE id=%d;" % row_id)
    if recovered_row != str(row_id):
        raise ConsoleAssertionError("Failover write %d was not visible on rebuilt A0" % row_id)
    ops.mark_time("T5_failover_rebuild_completed")

    ops.add_step(
        title="旧主重建为从库并回归",
        coverage="3",
        coverage_check="验证旧主拉平并重建为从库后恢复准入只读",
        action="使用 pg_basebackup 从新主 A1 重建 A0，控制台执行 SET NODE ACTIVE test_mmr1 并刷新拓扑",
        command="%s\n\n%s\n\n%s" % (
            rebuild_transcript, ops.console_result(set_active_a0), ops.console_result(refresh_a0)),
        intermediate=snap_rec.format_table(),
        evidence="\n".join(ops.extract_log_lines(["test_mmr1", "active", "replica", "AVAILABLE"], max_lines=6)),
        expected="A0 恢复为 active 读节点，candidate_type=READ，route_status=AVAILABLE",
        actual="A0 成功作为从库重新准入只读候选，直连可见切换期间写入 id=%s" % recovered_row,
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
    cleanup_parted = ops.admin_psql("SET NODE PARTED test_mmr1_s1;")
    ops.nodes.stop_node("A1", immediate=True)
    stop_a1 = ops.nodes.last_operation_transcript
    ops.nodes.promote_node("A0")
    promote_a0 = ops.nodes.last_operation_transcript
    time.sleep(2)
    cleanup_refresh_1 = ops.admin_psql("REFRESH CLUSTER site_a;")
    ops.nodes.rebuild_replica("A1", "A0")
    rebuild_a1 = ops.nodes.last_operation_transcript
    cleanup_active = ops.admin_psql("SET NODE ACTIVE test_mmr1_s1;")
    cleanup_refresh_2 = ops.admin_psql("REFRESH CLUSTER site_a;")
    time.sleep(2)
    cleanup_clusters = ops.admin_psql("SHOW CLUSTERS;")
    cleanup_routes = ops.admin_psql("SHOW GROUP_ROUTING qa_rep;")
    cleanup_clusters.assert_field({"cluster_name": "site_a"}, "current_primary", "test_mmr1")
    cleanup_routes.assert_field({"candidate_node": "test_mmr1", "candidate_type": "WRITE"}, "route_status", "AVAILABLE")
    cleanup_routes.assert_field({"candidate_node": "test_mmr1_s1", "candidate_type": "READ"}, "route_status", "AVAILABLE")
    cleanup_routes.assert_fields({"candidate_node": "test_mmr1", "user_name": "qa_hint_user"},
                                  effective_grouprole="primary", is_write_target="true")
    cleanup_routes.assert_fields({"candidate_node": "test_mmr1_s1", "user_name": "qa_hint_user"},
                                  effective_grouprole="replica", is_write_target="false")
    for label, port in (("A0", a0_port), ("A1", a1_port)):
        direct = ops._query_scalar(int(port),
                                  "SELECT id FROM qa_case.orders WHERE id=%d;" % row_id)
        if direct != str(row_id):
            raise ConsoleAssertionError("Failover write %d missing on restored %s" % (row_id, label))
    ops.add_step(
        title="恢复原始复制拓扑基线",
        action="将 A0 恢复为主库、A1 从 A0 重建为流复制备库，并恢复节点 ACTIVE",
        command="\n\n".join((ops.console_result(cleanup_parted), stop_a1, promote_a0,
                               ops.console_result(cleanup_refresh_1), rebuild_a1,
                               ops.console_result(cleanup_active), ops.console_result(cleanup_refresh_2))),
        intermediate="SHOW CLUSTERS:\n%s\n\nSHOW GROUP_ROUTING qa_rep:\n%s" % (
            cleanup_clusters.format_table(), cleanup_routes.format_table()),
        expected="site_a 主库恢复为 A0，A1 恢复为 AVAILABLE 只读备库",
        actual="current_primary=test_mmr1；A0 WRITE AVAILABLE；A1 READ AVAILABLE",
        result="PASS",
    )


def run_core_17_mmr_write_center_failover(context):
    """CORE-17: MMR write center automatic failover to promoted cluster and manual switch."""
    ops = context.ops
    ports = ops.env.config["database"]["ports"]
    a0_port = str(ports["mmr1"])
    b0_port = str(ports["mmr2"])
    ops.coverage_items = [
        "初始写中心验证：双向 MMR 组初始写中心为 site_a (A0 为唯一写目标，write_source=WRITE_CLUSTER)",
        "自动漂移至 promoted 中心：A0 故障后，写中心自动漂移至 B0 (write_source=PROMOTED_CLUSTER, fallback_reason=WRITE_CLUSTER_UNAVAILABLE)",
        "全局唯一写目标保证：全集群有且仅有 1 个写目标，物理写流量穿透至 %s 端口" % b0_port,
        "人工 SET NODE WRITE 锁定与组间隔离：人工指定写中心后 write_source=WRITE_CLUSTER，且其它组不受影响",
    ]
    ops.coverage_mapping = [
        ("1", "1", "MMR 初始写中心检查"),
        ("2", "2、3", "MMR 自动漂移至 promoted 中心"),
        ("3", "4", "人工 SET NODE WRITE 锁定与组隔离"),
    ]
    ops.overview_steps = [
        "检查 MMR 初始写中心为 A0",
        "停止 A0，验证自动漂移至 B0 及全局单写目标",
        "验证物理写流量穿透落点至 %s" % b0_port,
        "执行人工 SET NODE WRITE 锁定并验证组隔离",
    ]

    ops.start()
    ops.nodes.ensure_all_running()

    for node_name, port in (("A0", a0_port), ("B0", b0_port)):
        subscription_state = ops._query_scalar(
            int(port),
            "SELECT srsubstate FROM pg_subscription_rel "
            "WHERE srrelid='public.t_test1'::regclass LIMIT 1;",
        )
        if subscription_state != "r":
            raise ConsoleAssertionError(
                "MMR test table is not subscription-ready on %s: %s" %
                (node_name, subscription_state)
            )
        ops.add_step(
            title="%s MMR 数据同步基线" % node_name,
            command="SELECT srsubstate FROM pg_subscription_rel "
                    "WHERE srrelid='public.t_test1'::regclass LIMIT 1;",
            expected="public.t_test1 在 %s 的订阅状态为 r" % node_name,
            actual="srsubstate=%s" % subscription_state,
            result="PASS",
        )

    # Step 1: Initial baseline (A0 is write target, write_source=WRITE_CLUSTER)
    ops.console_step(
        "MMR 初始写中心检查", "SHOW GROUP_ROUTING qa_mmr;",
        verify=lambda s: (s.assert_field({"candidate_node": "test_mmr1"}, "is_write_target", "true"),
                          s.assert_field({"candidate_node": "test_mmr1"}, "write_source", "WRITE_CLUSTER"),
                          s.assert_write_target_count(1, user_name="postgres"),
                          s.assert_fields({"candidate_node": "test_mmr1", "user_name": "qa_hint_user"},
                                          candidate_type="WRITE", route_status="AVAILABLE")),
        coverage="1",
        coverage_check="验证双向多主组初始写中心配置",
        action="控制台执行 SHOW GROUP_ROUTING qa_mmr; 检查双向多主写中心",
        intermediate=lambda s: s.format_table(),
        expected="A0 (test_mmr1) 为唯一写中心，write_source=WRITE_CLUSTER",
        actual="A0 为唯一写中心，单用户下写目标数量唯一",
        result="PASS",
        checks=lambda s: [
            ReportCheck(
                title="A0 为初始写中心",
                expected="candidate_node=test_mmr1, is_write_target=true, write_source=WRITE_CLUSTER",
                actual=s.format_record(candidate_node="test_mmr1"),
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
    ops.mark_time("T1_stop_write_center")
    ops.nodes.stop_node("A0", immediate=True)
    stop_a0_transcript = ops.nodes.last_operation_transcript

    def write_drifted(snap):
        rows = snap.find_rows(candidate_node="test_mmr2", is_write_target="true")
        return rows and rows[0].get("write_source") == "PROMOTED_CLUSTER"

    snap_drift = ops.console_wait(
        "SHOW GROUP_ROUTING qa_mmr;", write_drifted,
        timeout=18, interval=1.0,
        describe="write center drifted to test_mmr2 (PROMOTED_CLUSTER)")
    snap_drift.assert_write_target_count(1, user_name="postgres")
    snap_drift.assert_field({"candidate_node": "test_mmr2"}, "is_write_target", "true")
    snap_drift.assert_field({"candidate_node": "test_mmr2"}, "write_source", "PROMOTED_CLUSTER")
    snap_drift.assert_field({"candidate_node": "test_mmr2"}, "fallback_reason", "WRITE_CLUSTER_UNAVAILABLE")
    snap_drift.assert_field({"candidate_node": "test_mmr2"}, "effective_state", "promoted")
    snap_drift.assert_field({"candidate_node": "test_mmr2"}, "effective_grouprole", "write-leader")
    for user in ("qa_app_user", "qa_hint_user", "postgres"):
        snap_drift.assert_write_target_count(1, user_name=user)
        snap_drift.assert_fields({"candidate_node": "test_mmr2", "user_name": user},
                                 candidate_type="WRITE", route_status="AVAILABLE")
        snap_drift.assert_fields({"candidate_node": "test_mmr2_s1", "user_name": user},
                                 candidate_type="READ", route_status="AVAILABLE")
        if snap_drift.find_rows(candidate_node="test_mmr1", user_name=user):
            raise ConsoleAssertionError("Faulted A0 remained an MMR route candidate")

    write_command, write_output, row_id, direct_output = ops.verify_write_insert(
        "qa_mmr", int(b0_port), table="public.t_test1"
    )
    ops.mark_time("T3_write_drift_published")

    ops.add_step(
        title="MMR 自动漂移至 promoted 中心",
        coverage="2、3",
        coverage_check="验证原写中心故障后自动漂移与全局单写目标",
        action="通过 SSH 停止 A0 (%s:%s)，等待路由自动漂移并通过客户端验证写入落点" % (ops.nodes.host, a0_port),
        command="%s\n\n客户端 INSERT RETURNING:\n%s\n输出: %s\n直连 B0 核对 id=%d: %s" % (
            stop_a0_transcript, write_command, write_output, row_id, direct_output),
        intermediate=snap_drift.format_table(),
        evidence="\n".join(ops.extract_log_lines(["qa_mmr", "drift", "promoted", "fallback", "test_mmr2"], max_lines=6)),
        expected="B0 成为唯一写中心，write_source=PROMOTED_CLUSTER，fallback_reason=WRITE_CLUSTER_UNAVAILABLE，物理落点 %s" % b0_port,
        actual="漂移成功，INSERT RETURNING 命中 %s 且直连 B0 查到 id=%d" % (b0_port, row_id),
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
                expected="INSERT RETURNING 端口为 %s，直连可查到新行" % b0_port,
                actual="代理返回=%s；直连行=%s" % (write_output, direct_output),
                result="PASS",
            ),
        ],
    )

    # Step 4: Recover A0
    ops.nodes.start_node("A0")
    recover_a0 = ops.nodes.last_operation_transcript
    ops.console_wait(
        "SHOW GROUP_ROUTING qa_rep;",
        lambda s: bool(s.find_rows(candidate_node="test_mmr1")),
        timeout=18, interval=1.0,
        retry=lambda: ops.admin_psql("REFRESH CLUSTER site_a;"),
        describe="test_mmr1 re-entered qa_rep routing",
        required=False)

    sync_deadline = time.monotonic() + 25
    recovered_row = None
    while time.monotonic() < sync_deadline:
        recovered_row = ops._query_scalar(
            int(a0_port), "SELECT id FROM public.t_test1 WHERE id=%d;" % row_id
        )
        if recovered_row == str(row_id):
            break
        time.sleep(1)
    if recovered_row != str(row_id):
        raise ConsoleAssertionError(
            "B0 write id=%d was not visible on recovered A0 within 25s" % row_id
        )
    ops.add_step(
        title="原写中心恢复后数据核对",
        action="直连 A0 查询 B0 故障接管期间写入的唯一主键",
        command="SELECT id FROM public.t_test1 WHERE id=%d;" % row_id,
        expected="A0 可查询到 B0 写入的 id=%d" % row_id,
        actual="A0 查询结果=%s" % recovered_row,
        result="PASS",
    )

    # Step 5: Manual switch via SET NODE WRITE ... IN GROUP qa_mmr
    refresh_a0 = ops.admin_psql("REFRESH CLUSTER site_a;")
    set_write = ops.admin_psql("SET NODE WRITE test_mmr2 IN GROUP qa_mmr;", title="人工命令锁定写中心为 B0")
    snap_manual = ops.admin_psql("SHOW GROUP_ROUTING qa_mmr;")
    snap_manual.assert_field({"candidate_node": "test_mmr2"}, "is_write_target", "true")
    snap_manual.assert_field({"candidate_node": "test_mmr2"}, "write_source", "WRITE_CLUSTER")

    # Group isolation check: other groups untouched
    snap_rep = ops.admin_psql("SHOW GROUP_ROUTING qa_rep;")
    snap_rep.assert_fields({"candidate_node": "test_mmr1", "user_name": "qa_hint_user"},
                           candidate_type="WRITE", is_write_target="true",
                           route_status="AVAILABLE")

    ops.add_step(
        title="人工 SET NODE WRITE 锁定与组隔离",
        coverage="4",
        coverage_check="验证人工命令锁定写中心与组配置隔离",
        action="控制台执行 SET NODE WRITE test_mmr2 IN GROUP qa_mmr; 锁定写中心，并检查 qa_rep 组隔离",
        command="%s\n\n%s\n\n%s" % (recover_a0, ops.console_result(refresh_a0), ops.console_result(set_write)),
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
    restore_write = ops.admin_psql("SET NODE WRITE test_mmr1 IN GROUP qa_mmr;")
    restore_refresh = ops.admin_psql("REFRESH CLUSTER site_a;")
    restored = ops.admin_psql("SHOW GROUP_ROUTING qa_mmr;")
    restored.assert_field({"candidate_node": "test_mmr1"}, "is_write_target", "true")
    restored.assert_field({"candidate_node": "test_mmr1"}, "write_source", "WRITE_CLUSTER")
    ops.add_step(
        title="恢复 MMR 初始写中心",
        command="%s\n\n%s" % (ops.console_result(restore_write), ops.console_result(restore_refresh)),
        intermediate=restored.format_table(),
        expected="qa_mmr 写中心恢复为 A0，write_source=WRITE_CLUSTER",
        actual="test_mmr1 is_write_target=true, write_source=WRITE_CLUSTER",
        result="PASS",
    )
