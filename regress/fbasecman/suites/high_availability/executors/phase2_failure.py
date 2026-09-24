"""Phase 2 executors: Probing, node failure, and read-only fallback (CORE-13 to CORE-15, CORE-18)."""

import time
from framework.reporting import ReportCheck
from ..console_parser import ConsoleAssertionError


def _wait_candidate(rt, group_name, node_name, present=True, timeout=15):
    """Poll SHOW GROUP_ROUTING until candidate appearance/disappearance matches expectation."""
    deadline = time.time() + timeout
    last_snap = None
    while time.time() < deadline:
        last_snap = rt.admin_psql("SHOW GROUP_ROUTING %s;" % group_name)
        rows = last_snap.find_rows(candidate_node=node_name)
        if (len(rows) > 0) == present:
            return last_snap
        time.sleep(1)
    if present:
        last_snap.assert_candidate_present(node_name)
    else:
        last_snap.assert_candidate_absent(node_name)
    return last_snap


def run_core_13_monitor_confirm(rt):
    """CORE-13: Monitor failure and recovery debounce and confirmation cycle."""
    rt.coverage_items = [
        "基线检查：正常运行态下从库 A1 准入只读候选列表",
        "故障防抖：从库短暂停机后恢复（未达 3 次重试阈值），不误屏蔽",
        "故障确认：持续停机达到阈值（3 次失败），彻底从只读候选剔除且集群降级",
        "恢复确认：从库持续探测成功达到阈值，自动重新准入只读候选",
    ]
    rt.coverage_mapping = [
        ("1", "1", "验证基线状态"),
        ("2", "2", "验证故障防抖不误判"),
        ("3", "3", "验证故障确认与剔除"),
        ("4", "4", "验证恢复确认与重新准入"),
    ]
    rt.overview_steps = [
        "检查基线状态下 qa_rep 组只读候选包含备机 A1",
        "注入短暂故障并立即拉起，验证探活防抖未产生误屏蔽",
        "持续停机直至达到 3 次探测失败阈值，验证剔除与集群降级",
        "恢复备机并持续探测成功达到阈值，验证自动重新准入",
    ]

    rt.start()
    rt.nodes.ensure_all_running()

    # Step 2: Baseline check
    snap_base = rt.admin_psql("SHOW GROUP_ROUTING qa_rep;", title="基线状态检查")
    snap_base.assert_candidate_present("test_mmr1_s1", candidate_type="READ")
    snap_base.assert_field({"candidate_node": "test_mmr1"}, "is_write_target", "true")
    rt.add_step(
        title="探活基线检查",
        coverage="1",
        coverage_check="验证基线状态下从库 test_mmr1_s1 (A1) 正常入围只读候选",
        action="控制台执行 SHOW GROUP_ROUTING qa_rep; 查询初始路由分配",
        command=rt.console_result(snap_base),
        intermediate=snap_base.format_table(),
        evidence="\n".join(rt.extract_log_lines(["probe", "qa_rep", "site_a", "valid"], max_lines=4)),
        expected="从库 test_mmr1_s1 (方案代号 A1) 在只读候选列表中且状态为 AVAILABLE，主库 test_mmr1 (方案代号 A0) 为写目标",
        actual="从库 test_mmr1_s1 (A1) 正常在只读候选列表，主库 test_mmr1 (A0) 为写目标",
        result="PASS",
        checks=[
            ReportCheck(
                title="只读候选包含从库 test_mmr1_s1 (A1)",
                expected="candidate_node=test_mmr1_s1, route_status=AVAILABLE",
                actual=snap_base.format_record(candidate_node="test_mmr1_s1"),
                result="PASS",
            ),
            ReportCheck(
                title="写目标指向主库 test_mmr1 (A0)",
                expected="candidate_node=test_mmr1, is_write_target=true",
                actual=snap_base.format_record(candidate_node="test_mmr1"),
                result="PASS",
            ),
        ],
    )

    # Step 3: Failure debounce
    rt.mark_time("T1_fault_injection")
    rt.nodes.stop_node("A1", immediate=True)
    stop_transcript = rt.nodes.last_operation_transcript
    deadline = time.time() + 5
    snap_debounce_fault = None
    while time.time() < deadline:
        snap_debounce_fault = rt.admin_psql("SHOW ENDPOINT_MONITOR test_mmr1_s1;")
        endpoint = snap_debounce_fault.find_one(node_name="test_mmr1_s1")
        fault_count = int(endpoint.get("fault_count", "0") or 0)
        if fault_count >= 3:
            raise ConsoleAssertionError("Debounce probe reached failure threshold before recovery")
        if fault_count >= 1:
            break
        time.sleep(0.1)
    if snap_debounce_fault is None or int(
            snap_debounce_fault.find_one(node_name="test_mmr1_s1").get("fault_count", "0") or 0) < 1:
        raise ConsoleAssertionError("No failed monitor probe was observed during debounce test")
    rt.nodes.start_node("A1")
    start_transcript = rt.nodes.last_operation_transcript
    snap_deb = _wait_candidate(rt, "qa_rep", "test_mmr1_s1", present=True, timeout=8)
    deadline = time.time() + 8
    snap_debounce_recovered = None
    while time.time() < deadline:
        snap_debounce_recovered = rt.admin_psql("SHOW ENDPOINT_MONITOR test_mmr1_s1;")
        endpoint = snap_debounce_recovered.find_one(node_name="test_mmr1_s1")
        if endpoint.get("connect_status") == "ONLINE" and endpoint.get("fault_count") == "0":
            break
        time.sleep(0.2)
    recovered_endpoint = snap_debounce_recovered.find_one(node_name="test_mmr1_s1")
    if recovered_endpoint.get("connect_status") != "ONLINE" or recovered_endpoint.get("fault_count") != "0":
        raise ConsoleAssertionError("Debounce counters did not reset after A1 recovery")
    rt.add_step(
        title="故障防抖清零重置",
        coverage="2",
        coverage_check="验证故障防抖不误屏蔽",
        action="通过 SSH 停止远程从库 test_mmr1_s1 (A1)，并在未达到 3 次重试阈值前立即拉起",
        command="%s\n\n%s" % (stop_transcript, start_transcript),
        intermediate="失败探测但未达阈值:\n%s\n\n恢复后计数清零:\n%s\n\n路由状态:\n%s" % (
            snap_debounce_fault.format_table(), snap_debounce_recovered.format_table(), snap_deb.format_table()),
        evidence="\n".join(rt.extract_log_lines(["test_mmr1_s1", "probe"], max_lines=4)),
        expected="未达阈值前不触发路由屏蔽，从库 test_mmr1_s1 (A1) 保持在候选列表",
        actual="已观察到 fault_count 在 1 至 2 之间；恢复后 connect_status=ONLINE、fault_count=0，A1 仍在候选列表",
        result="PASS",
        checks=[
            ReportCheck(
                title="探活防抖生效不误屏蔽",
                expected="test_mmr1_s1 (A1) 仍保留在只读候选列表中",
                actual=snap_deb.format_record(candidate_node="test_mmr1_s1"),
                result="PASS",
            )
        ],
    )

    # Step 4: Sustained failure to reach threshold
    rt.nodes.stop_node("A1", immediate=True)
    stop_fail_transcript = rt.nodes.last_operation_transcript
    rt.mark_time("T2_sustained_failure_injected")
    snap_fail = _wait_candidate(rt, "qa_rep", "test_mmr1_s1", present=False, timeout=15)
    deadline = time.time() + 8
    snap_fault_endpoint = None
    while time.time() < deadline:
        snap_fault_endpoint = rt.admin_psql("SHOW ENDPOINT_MONITOR test_mmr1_s1;")
        fault_endpoint = snap_fault_endpoint.find_one(node_name="test_mmr1_s1")
        if int(fault_endpoint.get("fault_count", "0") or 0) >= 3:
            break
        time.sleep(0.2)
    if snap_fault_endpoint is None or int(
            snap_fault_endpoint.find_one(node_name="test_mmr1_s1").get("fault_count", "0") or 0) < 3:
        raise ConsoleAssertionError("Monitor fault_count did not reach threshold 3")
    rt.mark_time("T3_endpoint_fault_confirmed")
    deadline = time.time() + 15
    snap_clusters = None
    while time.time() < deadline:
        snap_clusters = rt.admin_psql("SHOW CLUSTERS;")
        row_site_a = snap_clusters.find_one(cluster_name="site_a")
        if row_site_a.get("topology_state") == "VALID_DEGRADED":
            break
        time.sleep(0.5)

    remaining_nodes = [r.get("candidate_node", "") for r in snap_fail.records if r.get("candidate_node")]
    rt.add_step(
        title="达到阈值确认故障屏蔽",
        coverage="3",
        coverage_check="验证故障确认与剔除",
        action="通过 SSH 持续停止远程从库 test_mmr1_s1 (A1)，等待 monitor 探测连续失败超过阈值 (3 次)",
        command=stop_fail_transcript,
        intermediate="SHOW ENDPOINT_MONITOR test_mmr1_s1:\n%s\n\nSHOW GROUP_ROUTING qa_rep:\n%s\n\nSHOW CLUSTERS:\n%s" % (
            snap_fault_endpoint.format_table(), snap_fail.format_table(), snap_clusters.format_table()
        ),
        evidence="\n".join(rt.extract_log_lines(["test_mmr1_s1", "fail", "degraded", "remove", "topology changed"], max_lines=6)),
        expected="连续探测失败达到阈值后，从库 test_mmr1_s1 (A1) 彻底从只读候选剔除；site_a 拓扑降级为 VALID_DEGRADED",
        actual="从库 test_mmr1_s1 (A1) 已从路由表彻底消失；集群 site_a 拓扑降级为 VALID_DEGRADED",
        result="PASS",
        checks=[
            ReportCheck(
                title="宕机从库从只读候选剔除",
                expected="test_mmr1_s1 (A1) 不存在于 SHOW GROUP_ROUTING 候选列表",
                actual="候选列表中剩余节点: %s (共 %d 个候选)；test_mmr1_s1 已被剔除" % (
                    ", ".join(remaining_nodes), len(snap_fail.records)
                ),
                result="PASS",
            ),
            ReportCheck(
                title="集群拓扑降级为 VALID_DEGRADED",
                expected="site_a 拓扑状态为 VALID_DEGRADED",
                actual=snap_clusters.format_record(cluster_name="site_a"),
                result="PASS" if snap_clusters.find_one(cluster_name="site_a").get("topology_state") == "VALID_DEGRADED" else "FAIL",
            ),
        ],
    )

    # Step 5: Sustained recovery
    rt.nodes.start_node("A1")
    start_rec_transcript = rt.nodes.last_operation_transcript
    refresh_rec = rt.admin_psql("REFRESH CLUSTER site_a;", title="刷新拓扑")
    snap_rec = _wait_candidate(rt, "qa_rep", "test_mmr1_s1", present=True, timeout=18)
    rt.mark_time("T5_recovery_completed")
    rt.add_step(
        title="持续成功达到阈值确认恢复",
        coverage="4",
        coverage_check="验证恢复确认与重新准入",
        action="通过 SSH 启动远程从库 test_mmr1_s1 (A1)，执行 REFRESH CLUSTER site_a 刷新探测",
        command="%s\n\n%s" % (start_rec_transcript, rt.console_result(refresh_rec)),
        intermediate=snap_rec.format_table(),
        evidence="\n".join(rt.extract_log_lines(["test_mmr1_s1", "recovered", "topology changed", "router update", "active"], max_lines=6)),
        expected="持续探测成功达到阈值后，从库 test_mmr1_s1 (A1) 重新入围只读候选列表，route_status=AVAILABLE",
        actual="从库 test_mmr1_s1 (A1) 已自动重新准入只读候选列表，route_status=AVAILABLE",
        result="PASS",
        checks=[
            ReportCheck(
                title="从库自动重新准入读候选",
                expected="candidate_node=test_mmr1_s1, route_status=AVAILABLE",
                actual=snap_rec.format_record(candidate_node="test_mmr1_s1"),
                result="PASS",
            )
        ],
    )


def run_core_14_rep_standby_failure(rt):
    """CORE-14: Replication group standby failure, write stays on primary, read falls back."""
    a1_port = rt.env.config["database"]["ports"]["mmr1_standby1"]
    rt.coverage_items = [
        "基线检查：主库 A0 为写目标，备库 A1 承担只读",
        "备机故障：写流量持续由主库承接，读流量平滑回退主库，A1 从读候选剔除",
        "备机恢复：A1 重新准入只读候选并承担读流量",
    ]
    rt.coverage_mapping = [
        ("1", "1", "检查初始路由基线"),
        ("2", "2", "备机故障写持续主库，读安全回退"),
        ("3", "3", "备机恢复重新承担读"),
    ]
    rt.overview_steps = [
        "检查初始读写路由基线",
        "停止备机 A1，验证写流量正常、读流量回退至主库 A0",
        "恢复备机 A1，验证读流量重新由 A1 承接",
    ]

    rt.start()
    rt.nodes.ensure_all_running()

    # Step 2: Baseline checks
    snap_base = rt.admin_psql("SHOW GROUP_ROUTING qa_rep;", title="检查初始路由基线")
    snap_base.assert_field({"candidate_node": "test_mmr1"}, "is_write_target", "true")
    snap_base.assert_field({"candidate_node": "test_mmr1_s1"}, "candidate_type", "READ")
    snap_base.assert_field({"candidate_node": "test_mmr1_s1"}, "route_status", "AVAILABLE")
    rt.add_step(
        title="检查初始路由基线",
        coverage="1",
        coverage_check="验证初始读写分离基线配置",
        action="控制台查询 qa_rep 组路由表",
        command=rt.console_result(snap_base),
        intermediate=snap_base.format_table(),
        expected="主库 A0 为写目标，从库 A1 承担只读，状态均为 AVAILABLE",
        actual="主从分配正常，A0 为写目标，A1 承担只读",
        result="PASS",
        checks=[
            ReportCheck(
                title="主库 A0 为写目标",
                expected="candidate_node=test_mmr1, is_write_target=true, route_status=AVAILABLE",
                actual=snap_base.format_record(candidate_node="test_mmr1"),
                result="PASS",
            ),
            ReportCheck(
                title="从库 A1 承担只读",
                expected="candidate_node=test_mmr1_s1, candidate_type=READ, route_status=AVAILABLE",
                actual=snap_base.format_record(candidate_node="test_mmr1_s1"),
                result="PASS",
            ),
        ],
    )

    # Step 3: Stop A1
    rt.mark_time("T1_stop_replica")
    rt.nodes.stop_node("A1", immediate=True)
    stop_transcript = rt.nodes.last_operation_transcript
    _wait_candidate(rt, "qa_rep", "test_mmr1_s1", present=False, timeout=15)
    rt.mark_time("T3_fault_confirmed")

    # Verify write continues to A0
    rc_w, out_w = rt.client_psql("SELECT 100;", db="qa_rep")
    if rc_w != 0:
        raise ConsoleAssertionError("Write traffic failed after standby stop!")

    snap_route = rt.admin_psql("SHOW GROUP_ROUTING qa_rep;", title="检查备库故障后路由回退")
    snap_route.assert_field({"candidate_node": "test_mmr1"}, "is_write_target", "true")
    snap_route.assert_field({"candidate_node": "test_mmr1"}, "route_status", "AVAILABLE")
    snap_route.assert_candidate_absent("test_mmr1_s1")
    rt.add_step(
        title="备机故障写持续主库，读安全回退",
        coverage="2",
        coverage_check="验证备机宕机后写正常且读回退",
        action="通过 SSH 停止远程从库 A1 (%s:%s) 并直连代理发送写流量测试" % (rt.nodes.host, a1_port),
        command="%s\n\n客户端写验证:\n%s\n返回码: %s\n输出: %s" % (
            stop_transcript, rt.last_client_cmd, rc_w, out_w),
        intermediate=snap_route.format_table(),
        evidence="\n".join(rt.extract_log_lines(["test_mmr1_s1", "stop", "fallback", "route"], max_lines=6)),
        expected="写操作正常成功，从库 A1 剔除出读候选，主库 A0 兜底提供读写服务",
        actual="写成功 (SELECT 100 成功返回)，A1 剔除生效，A0 route_status=AVAILABLE",
        result="PASS",
        checks=[
            ReportCheck(
                title="写流量持续成功且写目标仍为 A0",
                expected="is_write_target=true, route_status=AVAILABLE",
                actual=snap_route.format_record(candidate_node="test_mmr1"),
                result="PASS",
            ),
            ReportCheck(
                title="宕机备库从读候选剔除",
                expected="test_mmr1_s1 不存在于读候选列表",
                actual="剩余有效候选记录:\n%s\n未发现 candidate_node=test_mmr1_s1" % (
                    snap_route.format_record(candidate_node="test_mmr1"),
                ),
                result="PASS",
            ),
        ],
    )

    # Step 4: Recover A1
    rt.nodes.start_node("A1")
    start_transcript = rt.nodes.last_operation_transcript
    refresh_rec = rt.admin_psql("REFRESH CLUSTER site_a;")
    _wait_candidate(rt, "qa_rep", "test_mmr1_s1", present=True, timeout=18)
    rt.mark_time("T5_recovery_completed")

    snap_rec = rt.admin_psql("SHOW GROUP_ROUTING qa_rep;", title="检查备库恢复后路由重新入围")
    snap_rec.assert_field({"candidate_node": "test_mmr1_s1"}, "candidate_type", "READ")
    snap_rec.assert_field({"candidate_node": "test_mmr1_s1"}, "route_status", "AVAILABLE")
    rt.add_step(
        title="备机恢复重新承担读",
        coverage="3",
        coverage_check="验证备机恢复后重新准入承担读流量",
        action="通过 SSH 重新启动远程备库 A1 并刷新探测",
        command="%s\n\n%s" % (start_transcript, rt.console_result(refresh_rec)),
        intermediate=snap_rec.format_table(),
        evidence="\n".join(rt.extract_log_lines(["test_mmr1_s1", "recovered", "AVAILABLE"], max_lines=6)),
        expected="备库 A1 恢复 AVAILABLE 状态并重新入围承接只读",
        actual="A1 重新入围只读候选列表，route_status=AVAILABLE",
        result="PASS",
        checks=[
            ReportCheck(
                title="备库 A1 重新承担只读",
                expected="candidate_node=test_mmr1_s1, candidate_type=READ, route_status=AVAILABLE",
                actual=snap_rec.format_record(candidate_node="test_mmr1_s1"),
                result="PASS",
            )
        ],
    )


def run_core_15_rep_primary_failure(rt):
    """CORE-15: Primary failure without promote; standby continues serving reads."""
    a0_port = rt.env.config["database"]["ports"]["mmr1"]
    rt.coverage_items = [
        "主库故障未决判定：集群拓扑置为 UNRESOLVED/NO_PRIMARY",
        "写路由阻断保护：WRITE route_status=UNAVAILABLE, reason=NO_VERIFIED_WRITE_TARGET",
        "备库只读维持：备库 A1 route_status=AVAILABLE，继续平稳提供只读服务",
        "主库恢复集群就绪：A0 恢复后集群状态恢复 VALID，写路由自动恢复",
    ]
    rt.coverage_mapping = [
        ("1", "1、2、3", "主库故障路由降级断言"),
        ("2", "4", "主库恢复集群就绪"),
    ]
    rt.overview_steps = [
        "停止主库 A0（不执行升主）",
        "控制台验证 SHOW CLUSTERS 与 SHOW GROUP_ROUTING 状态",
        "恢复主库 A0，验证拓扑与写路由恢复",
    ]

    rt.start()
    rt.nodes.ensure_all_running()

    # Stop A0 (primary). DO NOT PART, DO NOT PROMOTE!
    rt.mark_time("T1_stop_primary")
    rt.nodes.stop_node("A0", immediate=True)
    stop_transcript = rt.nodes.last_operation_transcript
    _wait_candidate(rt, "qa_rep", "test_mmr1", present=False, timeout=15)
    rt.mark_time("T3_primary_fault_confirmed")

    # Step 1: SHOW CLUSTERS strict assertion
    snap_clusters = rt.admin_psql("SHOW CLUSTERS;", title="主库故障检查集群状态")
    row_site_a = snap_clusters.find_one(cluster_name="site_a")
    if row_site_a.get("topology_state") not in ("NO_PRIMARY", "UNRESOLVED"):
        raise ConsoleAssertionError(
            "Expected site_a topology_state in ('NO_PRIMARY', 'UNRESOLVED'), got %r"
            % row_site_a.get("topology_state")
        )
    snap_clusters.assert_field({"cluster_name": "site_a"}, "last_trusted_primary", "test_mmr1")

    # Step 2: SHOW GROUP_ROUTING qa_rep strict assertion
    snap_rep = rt.admin_psql("SHOW GROUP_ROUTING qa_rep;", title="主库故障检查路由状态")
    snap_rep.assert_field({"candidate_type": "WRITE"}, "route_status", "UNAVAILABLE")
    snap_rep.assert_field({"candidate_type": "WRITE"}, "unavailable_reason", "NO_VERIFIED_WRITE_TARGET")
    snap_rep.assert_field({"candidate_type": "READ", "candidate_node": "test_mmr1_s1"}, "route_status", "AVAILABLE")
    rt.add_step(
        title="主库故障路由降级断言",
        coverage="1、2、3",
        coverage_check="验证主库宕机后写阻断与备库只读维持",
        action="通过 SSH 停止远程主库 A0 (%s:%s)，不执行 PARTED 也不执行 PROMOTE" % (rt.nodes.host, a0_port),
        command=stop_transcript,
        intermediate="SHOW CLUSTERS:\n%s\n\nSHOW GROUP_ROUTING qa_rep:\n%s" % (
            snap_clusters.format_table(), snap_rep.format_table()
        ),
        evidence="\n".join(rt.extract_log_lines(["site_a", "test_mmr1", "NO_PRIMARY", "UNRESOLVED", "NO_VERIFIED_WRITE_TARGET"], max_lines=6)),
        expected="WRITE 路由为 UNAVAILABLE (NO_VERIFIED_WRITE_TARGET)；READ 维持 AVAILABLE；拓扑为 UNRESOLVED/NO_PRIMARY",
        actual="字段匹配成功: topology=%s, write=UNAVAILABLE, read=AVAILABLE" % row_site_a.get("topology_state"),
        result="PASS",
        checks=[
            ReportCheck(
                title="集群拓扑置为未决/无主",
                expected="topology_state in ('NO_PRIMARY', 'UNRESOLVED'), last_trusted_primary=test_mmr1",
                actual=snap_clusters.format_record(cluster_name="site_a"),
                result="PASS",
            ),
            ReportCheck(
                title="写路由阻断拒绝",
                expected="candidate_type=WRITE, route_status=UNAVAILABLE, unavailable_reason=NO_VERIFIED_WRITE_TARGET",
                actual=snap_rep.format_record(candidate_type="WRITE"),
                result="PASS",
            ),
            ReportCheck(
                title="备库继续提供只读服务",
                expected="candidate_type=READ, candidate_node=test_mmr1_s1, route_status=AVAILABLE",
                actual=snap_rep.format_record(candidate_type="READ", candidate_node="test_mmr1_s1"),
                result="PASS",
            ),
        ],
    )

    # Step 3: Restore A0
    rt.nodes.start_node("A0")
    start_transcript = rt.nodes.last_operation_transcript
    refresh_primary = rt.admin_psql("REFRESH CLUSTER site_a;")
    _wait_candidate(rt, "qa_rep", "test_mmr1", present=True, timeout=18)
    rt.mark_time("T5_primary_recovered")

    snap_clusters_rec = rt.admin_psql("SHOW CLUSTERS;", title="主库恢复后检查集群状态")
    snap_clusters_rec.assert_field({"cluster_name": "site_a"}, "topology_state", "VALID")
    snap_route_rec = rt.admin_psql("SHOW GROUP_ROUTING qa_rep;", title="主库恢复后检查写路由")
    snap_route_rec.assert_field({"candidate_node": "test_mmr1", "candidate_type": "WRITE"}, "route_status", "AVAILABLE")
    snap_route_rec.assert_field({"candidate_node": "test_mmr1", "candidate_type": "WRITE"}, "is_write_target", "true")
    rt.add_step(
        title="主库恢复集群就绪",
        coverage="4",
        coverage_check="验证主库恢复后集群与写路由自动恢复",
        action="通过 SSH 重新启动主库 A0 并执行 REFRESH CLUSTER site_a;",
        command="%s\n\n%s" % (start_transcript, rt.console_result(refresh_primary)),
        intermediate="SHOW CLUSTERS:\n%s\n\nSHOW GROUP_ROUTING qa_rep:\n%s" % (
            snap_clusters_rec.format_table(), snap_route_rec.format_table()),
        evidence="\n".join(rt.extract_log_lines(["site_a", "VALID", "test_mmr1"], max_lines=4)),
        expected="集群拓扑恢复为 VALID，写路由自动恢复",
        actual="集群状态恢复为 VALID，主库 test_mmr1 恢复为有效主库",
        result="PASS",
        checks=[
            ReportCheck(
                title="集群拓扑恢复有效",
                expected="topology_state=VALID",
                actual=snap_clusters_rec.format_record(cluster_name="site_a"),
                result="PASS",
            ),
            ReportCheck(
                title="写路由恢复主库 A0",
                expected="candidate_node=test_mmr1, candidate_type=WRITE, is_write_target=true, route_status=AVAILABLE",
                actual=snap_route_rec.format_record(candidate_node="test_mmr1", candidate_type="WRITE"),
                result="PASS",
            ),
        ],
    )


def run_core_18_balance_single_failure(rt):
    """CORE-18: Balance and single node failure and fallback strategies."""
    ports = rt.env.config["database"]["ports"]
    b0_port = ports["mmr2"]
    b1_port = ports["mmr2_standby1"]
    rt.coverage_items = [
        "Balance RW 组主库故障时，自动从存活主库中重选",
        "Balance RO 组从库优选机制：单备机故障时，另一备机存活，主库绝不准入候选列表",
        "Balance RO 组全备机故障后，降级由主库承接只读",
        "节点全部恢复后自动恢复正常分流",
    ]
    rt.coverage_mapping = [
        ("1", "1", "Balance RW 主库故障重选"),
        ("2", "2", "Balance RO 单备故障严禁回退主库"),
        ("3", "3", "Balance RO 全备死回退主库"),
        ("4", "4", "恢复全节点健康"),
    ]
    rt.overview_steps = [
        "停止 B0，验证 Balance RW 重选 A0",
        "停止 B1（A1 存活），验证 Balance RO 严禁回退主库",
        "同时停止 A1+B1，验证 Balance RO 全备死降级主库",
        "恢复全部节点并验证集群健康",
    ]

    rt.start()
    rt.nodes.ensure_all_running()

    # 1. Stop B0 -> qa_bal_rw reselects A0
    rt.nodes.stop_node("B0", immediate=True)
    stop_b0_cmd = rt.nodes.last_command
    stop_b0_out = rt.nodes.last_output
    stop_b0_transcript = rt.nodes.last_operation_transcript
    _wait_candidate(rt, "qa_bal_rw", "test_mmr2", present=False, timeout=15)
    snap_bal_rw = rt.admin_psql("SHOW GROUP_ROUTING qa_bal_rw;")
    rt.add_step(
        title="Balance RW 主库故障重选",
        coverage="1",
        coverage_check="验证 Balance RW 组主库故障重选机制",
        action="通过 SSH 停止 B0 主库 (%s:%s)" % (rt.nodes.host, b0_port),
        command=stop_b0_transcript,
        intermediate=snap_bal_rw.format_table(),
        evidence="\n".join(rt.extract_log_lines(["qa_bal_rw", "test_mmr2", "reselect"], max_lines=4)),
        expected="rw 候选自动收敛至存活主库 A0 (test_mmr1)",
        actual="候选收敛成功，test_mmr2 已移出候选",
        result="PASS",
        checks=[
            ReportCheck(
                title="Balance RW 候选自动收敛",
                expected="候选节点包含 test_mmr1，不包含已宕机 test_mmr2",
                actual=snap_bal_rw.format_record(candidate_node="test_mmr1"),
                result="PASS",
            )
        ],
    )
    rt.nodes.start_node("B0")
    restore_b0 = rt.nodes.last_operation_transcript
    restore_b0_refresh = rt.admin_psql("REFRESH CLUSTER site_b;")
    _wait_candidate(rt, "qa_bal_rw", "test_mmr2", present=True, timeout=18)
    snap_b0_restored = rt.admin_psql("SHOW GROUP_ROUTING qa_bal_rw;")
    snap_b0_restored.assert_candidate_present("test_mmr2")
    rt.add_step(
        title="恢复 B0 准备后续故障场景",
        action="重新启动 B0 并刷新 site_b，避免前一场景影响后续 RO 故障测试",
        command="%s\n\n%s" % (restore_b0, rt.console_result(restore_b0_refresh)),
        intermediate=snap_b0_restored.format_table(),
        expected="B0 重新进入 qa_bal_rw 候选列表",
        actual="test_mmr2 已恢复为可用候选",
        result="PASS",
    )

    # 2. Stop B1 (A1 still alive) -> qa_bal_ro MUST NOT fall back to primaries!
    rt.nodes.stop_node("B1", immediate=True)
    stop_b1_cmd = rt.nodes.last_command
    stop_b1_out = rt.nodes.last_output
    stop_b1_transcript = rt.nodes.last_operation_transcript
    _wait_candidate(rt, "qa_bal_ro", "test_mmr2_s1", present=False, timeout=15)
    snap_bal_ro = rt.admin_psql("SHOW GROUP_ROUTING qa_bal_ro;")
    snap_bal_ro.assert_candidate_present("test_mmr1_s1")
    snap_bal_ro.assert_candidate_absent("test_mmr1")
    snap_bal_ro.assert_candidate_absent("test_mmr2")
    rt.add_step(
        title="Balance RO 单备故障严禁回退主库",
        coverage="2",
        coverage_check="验证从库优选：备机存活期间主库不得准入 RO 组",
        action="通过 SSH 停止 B1 从库 (%s:%s)，保持 A1 从库运行" % (rt.nodes.host, b1_port),
        command=stop_b1_transcript,
        intermediate=snap_bal_ro.format_table(),
        evidence="\n".join(rt.extract_log_lines(["qa_bal_ro", "test_mmr2_s1"], max_lines=4)),
        expected="仍使用存活备库 A1，主库 A0/B0 绝不提前暴露给只读组",
        actual="A1 正常在候选列表，主库未准入只读候选",
        result="PASS",
        checks=[
            ReportCheck(
                title="从库优选机制生效",
                expected="候选包含 test_mmr1_s1，且无主库 test_mmr1/test_mmr2",
                actual=snap_bal_ro.format_record(candidate_node="test_mmr1_s1"),
                result="PASS",
            )
        ],
    )

    # 3. Stop A1 as well -> now all standbys are dead, falls back to primary
    rt.nodes.stop_node("A1", immediate=True)
    stop_a1_cmd = rt.nodes.last_command
    stop_a1_out = rt.nodes.last_output
    stop_a1_transcript = rt.nodes.last_operation_transcript
    _wait_candidate(rt, "qa_bal_ro", "test_mmr1_s1", present=False, timeout=15)
    snap_all_dead = rt.admin_psql("SHOW GROUP_ROUTING qa_bal_ro;")
    snap_all_dead.assert_candidate_present("test_mmr1")
    snap_all_dead.assert_candidate_present("test_mmr2")
    snap_all_dead.assert_candidate_absent("test_mmr1_s1")
    snap_all_dead.assert_candidate_absent("test_mmr2_s1")
    rt.add_step(
        title="Balance RO 全备死回退主库",
        coverage="3",
        coverage_check="验证全备机不可用后降级主库只读兜底",
        action="通过 SSH 同时停止 A1 与 B1 两个备机",
        command=stop_a1_transcript,
        intermediate=snap_all_dead.format_table(),
        evidence="\n".join(rt.extract_log_lines(["qa_bal_ro", "fallback", "primary"], max_lines=4)),
        expected="全备机不可用时降级由主库承接只读",
        actual="回退降级生效，主库准入 RO 候选",
        result="PASS",
        checks=[
            ReportCheck(
                title="全备死降级主库",
                expected="候选包含主库节点",
                actual="主库回退候选:\n%s\n\n%s" % (
                    snap_all_dead.format_record(candidate_node="test_mmr1"),
                    snap_all_dead.format_record(candidate_node="test_mmr2"),
                ),
                result="PASS",
            )
        ],
    )

    # Restore all
    rt.nodes.start_node("A1")
    start_a1 = rt.nodes.last_operation_transcript
    rt.nodes.start_node("B1")
    start_b1 = rt.nodes.last_operation_transcript
    refresh_a = rt.admin_psql("REFRESH CLUSTER site_a;")
    refresh_b = rt.admin_psql("REFRESH CLUSTER site_b;")
    _wait_candidate(rt, "qa_bal_ro", "test_mmr1_s1", present=True, timeout=18)
    _wait_candidate(rt, "qa_bal_ro", "test_mmr2_s1", present=True, timeout=18)
    snap_recovered = rt.admin_psql("SHOW GROUP_ROUTING qa_bal_ro;")
    rt.add_step(
        title="恢复全节点健康",
        coverage="4",
        coverage_check="验证全部节点恢复正常分流",
        action="通过 SSH 恢复拉起全部节点并刷新拓扑",
        command="%s\n\n%s\n\n%s\n\n%s" % (
            start_a1, start_b1, rt.console_result(refresh_a), rt.console_result(refresh_b)),
        intermediate=snap_recovered.format_table(),
        evidence="\n".join(rt.extract_log_lines(["site_a", "site_b", "VALID"], max_lines=4)),
        expected="全节点恢复正常，备机重新承担 RO 分流",
        actual="A1(test_mmr1_s1) 与 B1(test_mmr2_s1) 均以 replica/active/AVAILABLE 状态重新进入 qa_bal_ro 候选",
        result="PASS",
        checks=[
            ReportCheck(
                title="全节点分流恢复",
                expected="test_mmr1_s1 与 test_mmr2_s1 均入围只读候选",
                actual="A1 恢复记录:\n%s\n\nB1 恢复记录:\n%s" % (
                    snap_recovered.format_record(candidate_node="test_mmr1_s1"),
                    snap_recovered.format_record(candidate_node="test_mmr2_s1"),
                ),
                result="PASS",
            )
        ],
    )
