"""Phase 1 executors: Console commands, atomicity, and Reload protection (CORE-19 to CORE-22)."""

import os
import re
import time
import hashlib

from ..console_parser import ConsoleAssertionError


def _digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _rep_route_ready(snap):
    write = snap.find_rows(candidate_node="test_mmr1", candidate_type="WRITE")
    read = snap.find_rows(candidate_node="test_mmr1_s1", candidate_type="READ")
    return (write and write[0].get("route_status") == "AVAILABLE" and
            read and read[0].get("route_status") == "AVAILABLE")


def _wait_rep_route_ready(context, timeout=15):
    return context.ops.console_wait(
        "SHOW GROUP_ROUTING qa_rep;", _rep_route_ready,
        timeout=timeout, interval=1.0,
        describe="qa_rep WRITE(test_mmr1)/READ(test_mmr1_s1) candidates AVAILABLE")


def _wait_mmr_candidate(context, node_name, write_target=False, timeout=20):
    def ready(snap):
        rows = snap.find_rows(candidate_node=node_name, user_name="qa_app_user")
        return (rows and rows[0].get("route_status") == "AVAILABLE" and
                (not write_target or rows[0].get("is_write_target") == "true"))
    return context.ops.console_wait(
        "SHOW GROUP_ROUTING qa_mmr;", ready, timeout=timeout, interval=0.5,
        describe="MMR candidate %s %s"
        % (node_name, "WRITE" if write_target else "AVAILABLE"))


def _wait_mmr_endpoint_ready(context, node_name, timeout=20):
    def ready(snap):
        row = snap.find_one(node_name=node_name)
        return (row.get("probe_state") == "READY" and
                row.get("connect_status") == "ONLINE" and
                row.get("topology_state") == "VALID" and
                row.get("fault_flags") == "{}")
    return context.ops.console_wait(
        "SHOW ENDPOINT_MONITOR %s;" % node_name, ready,
        timeout=timeout, interval=0.25,
        describe="MMR endpoint %s ready" % node_name)


def run_core_19_set_node_atomicity(context):
    """CORE-19: SET NODE single, batch atomicity, and restart persistence."""
    ops = context.ops
    conf = ops.start()

    ops.console_step(
        "初始运行态检查", "SHOW GROUP_ROUTING qa_rep;",
        verify=lambda s: (s.assert_candidate_present("test_mmr1_s1", "READ"),
                          s.assert_field({"candidate_node": "test_mmr1_s1"},
                                         "effective_state", "active")),
        expected="A1 为 active 只读候选",
        actual=lambda s: s.format_record(candidate_node="test_mmr1_s1"))

    before_conf = conf.read_text(encoding="utf-8")
    set_parted = ops.admin_psql("SET NODE PARTED test_mmr1_s1;")
    snap_parted_route = ops.admin_psql("SHOW GROUP_ROUTING qa_rep;")
    snap_parted_route.assert_candidate_absent("test_mmr1_s1")
    snap_parted_route.assert_fields(
        {"user_name": "qa_app_user", "candidate_type": "READ"},
        candidate_node="", route_status="UNAVAILABLE", unavailable_reason="NO_READ_CANDIDATE")
    snap_parted_members = ops.admin_psql("SHOW GROUP_MEMBERS;")
    snap_parted_members.assert_field({"node_name": "test_mmr1_s1", "group_name": "qa_rep"}, "state", "parted")
    snap_parted_members.assert_fields(
        {"node_name": "test_mmr1_s1", "group_name": "qa_rep"},
        config_status="parted", group_role="replica")
    after_conf = conf.read_text(encoding="utf-8")
    config_diff = ops.diff_text(before_conf, after_conf, "操作前配置", "操作后配置")
    if not config_diff or 'status "parted"' not in config_diff:
        raise ConsoleAssertionError("SET NODE PARTED did not persist the expected datasource status change")
    ops.add_step("SET NODE PARTED test_mmr1_s1", command=ops.console_result(set_parted),
                intermediate="配置文件变更:\n%s\n\n成员状态:\n%s\n\n路由状态:\n%s" % (
                    config_diff, snap_parted_members.format_record(node_name="test_mmr1_s1", group_name="qa_rep"),
                    snap_parted_route.format_table()),
                expected="effective_state=parted，路由剔除，且配置文件持久化 status=parted",
                actual="控制台命令成功；成员状态为 parted；路由中无 A1；配置 diff 包含 status=parted", result="PASS")

    set_active = ops.admin_psql("SET NODE ACTIVE test_mmr1_s1;")
    snap_pending = ops.admin_psql("SHOW GROUP_ROUTING qa_rep;")
    refresh_active = ops.admin_psql("REFRESH CLUSTER site_a;")
    snap_active = _wait_rep_route_ready(context)
    snap_active.assert_candidate_present("test_mmr1_s1", "READ")
    snap_active.assert_field({"candidate_node": "test_mmr1_s1"}, "effective_state", "active")
    snap_active.assert_fields(
        {"candidate_node": "test_mmr1_s1", "user_name": "qa_app_user"},
        candidate_type="READ", route_status="AVAILABLE")
    ops.add_step("SET NODE ACTIVE test_mmr1_s1", command="%s\n\n%s" % (
                    ops.console_result(set_active), ops.console_result(refresh_active)),
                intermediate="ACTIVE 后即时路由:\n%s\n\n复探确认后路由:\n%s" %
                             (snap_pending.format_table(), snap_active.format_table()),
                expected="A1 恢复为 active 只读候选", actual="A1 effective_state=active, candidate_type=READ", result="PASS")

    batch_parted = ops.admin_psql("SET NODE PARTED test_mmr1, test_mmr1_s1, test_mmr1;")
    snap_batch_route = ops.admin_psql("SHOW GROUP_ROUTING qa_rep;")
    snap_batch_route.assert_candidate_absent("test_mmr1")
    snap_batch_route.assert_candidate_absent("test_mmr1_s1")
    snap_batch_members = ops.admin_psql("SHOW GROUP_MEMBERS;")
    snap_batch_members.assert_field({"node_name": "test_mmr1", "group_name": "qa_rep"}, "state", "parted")
    snap_batch_members.assert_field({"node_name": "test_mmr1_s1", "group_name": "qa_rep"}, "state", "parted")
    ops.add_step("批量合法 PARTED 去重", command=ops.console_result(batch_parted),
                intermediate=snap_batch_members.format_table(),
                expected="重复节点去重执行，A0/A1 均为 parted 并从路由剔除",
                actual="A0/A1 成员状态均为 parted，路由候选中均不存在", result="PASS")

    ops.admin_psql("SET NODE ACTIVE test_mmr1, test_mmr1_s1;")
    ops.admin_psql("REFRESH CLUSTER site_a;")
    _wait_rep_route_ready(context)

    before_conf_text = conf.read_text(encoding="utf-8")
    rejected = ops.admin_psql("SET NODE PARTED test_mmr1_s1, NON_EXISTENT_NODE_XYZ;", check=False)
    if rejected.returncode == 0:
        raise ConsoleAssertionError("Expected invalid batch SET NODE command to fail")

    snap_check = ops.admin_psql("SHOW GROUP_ROUTING qa_rep;")
    snap_check.assert_candidate_present("test_mmr1_s1", "READ")
    snap_check.assert_field({"candidate_node": "test_mmr1_s1"}, "effective_state", "active")
    snap_check.assert_fields(
        {"candidate_node": "test_mmr1_s1", "user_name": "qa_app_user"},
        candidate_type="READ", route_status="AVAILABLE")
    rejected.assert_error("NON_EXISTENT_NODE_XYZ", "does not exist")

    after_conf_text = conf.read_text(encoding="utf-8")
    if before_conf_text != after_conf_text:
        raise ConsoleAssertionError("Config file was partially modified during failed batch command!")

    ops.add_step("批量原子性拦截非法节点", command=ops.console_result(rejected),
                intermediate="失败后路由状态:\n%s\n\n配置文件 diff:\n<无差异>" % snap_check.format_record(candidate_node="test_mmr1_s1"),
                expected="命令整体拒绝；A1 保持 active；配置文件无部分写入",
                actual="返回码=%s；A1 effective_state=active；操作前后配置逐字节一致" % rejected.returncode, result="PASS")

    cross_parted = ops.admin_psql("SET NODE PARTED test_mmr1_s1, test_mmr2_s1;")
    parted_sources = ops.admin_psql("SHOW DATASOURCES;")
    for node in ("test_mmr1_s1", "test_mmr2_s1"):
        parted_sources.assert_fields({"node_name": node}, config_status="parted")
    for node in ("test_mmr1", "test_mmr2"):
        parted_sources.assert_fields({"node_name": node}, config_status="active")
    fallback = ops.admin_psql("SHOW GROUP_ROUTING qa_bal_ro;")
    for node in ("test_mmr1", "test_mmr2"):
        fallback.assert_fields({"candidate_node": node, "user_name": "qa_app_user"},
                               candidate_type="ROUTE", effective_grouprole="primary",
                               route_status="AVAILABLE")
    fallback.assert_candidate_absent("test_mmr1_s1")
    fallback.assert_candidate_absent("test_mmr2_s1")
    cross_active = ops.admin_psql("SET NODE ACTIVE test_mmr1_s1, test_mmr2_s1;")
    refresh_a = ops.admin_psql("REFRESH CLUSTER site_a;")
    refresh_b = ops.admin_psql("REFRESH CLUSTER site_b;")
    recovered_balance = ops.console_wait(
        "SHOW GROUP_ROUTING qa_bal_ro;",
        lambda s: all(s.find_rows(candidate_node=node, user_name="qa_app_user")
                      for node in ("test_mmr1_s1", "test_mmr2_s1")),
        timeout=20, interval=0.5,
        describe="balance read candidates recovered")
    for node in ("test_mmr1_s1", "test_mmr2_s1"):
        recovered_balance.assert_fields({"candidate_node": node, "user_name": "qa_app_user"},
                                        candidate_type="ROUTE", effective_grouprole="replica",
                                        route_status="AVAILABLE")
    recovered_balance.assert_candidate_absent("test_mmr1")
    recovered_balance.assert_candidate_absent("test_mmr2")
    ops.add_step("跨 cluster 批量隔离与恢复", command="\n\n".join((
        ops.console_result(cross_parted), ops.console_result(cross_active),
        ops.console_result(refresh_a), ops.console_result(refresh_b))),
        intermediate="SHOW DATASOURCES 隔离后:\n%s\n\n回退路由:\n%s\n\n恢复路由:\n%s" % (
            parted_sources.format_table(), fallback.format_table(), recovered_balance.format_table()),
        evidence="\n".join(ops.extract_log_lines(["test_mmr1_s1", "test_mmr2_s1", "config"], max_lines=8)),
        expected="两备 PARTED、两主 active；RO 回退两主；ACTIVE 复探后只选两备",
        actual="两备配置状态 parted；回退 A0/B0；恢复 A1/B1 为 replica/ROUTE/AVAILABLE",
        result="PASS")

    persist_parted = ops.admin_psql("SET NODE PARTED test_mmr1_s1;")
    ops.restart()
    snap_restart_route = ops.admin_psql("SHOW GROUP_ROUTING qa_rep;")
    snap_restart_route.assert_candidate_absent("test_mmr1_s1")
    snap_restart_members = ops.admin_psql("SHOW GROUP_MEMBERS;")
    snap_restart_members.assert_field({"node_name": "test_mmr1_s1", "group_name": "qa_rep"}, "state", "parted")
    snap_restart_members.assert_field(
        {"node_name": "test_mmr1_s1", "group_name": "qa_rep"}, "config_status", "parted")
    ops.add_step("重启后配置持久化生效", command=ops.console_result(persist_parted),
                intermediate=snap_restart_members.format_record(node_name="test_mmr1_s1", group_name="qa_rep"),
                expected="进程重启后 A1 仍为 parted 且不在路由候选中",
                actual="A1 state=parted，重启后路由中无 A1", result="PASS")

    cleanup_active = ops.admin_psql("SET NODE ACTIVE test_mmr1_s1;")
    cleanup_refresh = ops.admin_psql("REFRESH CLUSTER site_a;")
    cleanup_route = _wait_rep_route_ready(context)
    ops.add_step("恢复用例基线", command="%s\n\n%s" % (
                    ops.console_result(cleanup_active), ops.console_result(cleanup_refresh)),
                intermediate=cleanup_route.format_table(),
                expected="A1 恢复 active/READ/AVAILABLE", actual=cleanup_route.format_record(candidate_node="test_mmr1_s1"), result="PASS")


def run_core_20_write_promoted_refresh_show(context):
    """CORE-20: Role commands, refresh, and comprehensive show output audit."""
    ops = context.ops
    conf = ops.start()
    snap_ready = _wait_mmr_candidate(context, "test_mmr2")
    endpoint_ready = _wait_mmr_endpoint_ready(context, "test_mmr2")
    ops.add_step(
        "MMR 候选发布基线",
        command=ops.console_result(snap_ready),
        expected="B0 已进入 qa_mmr 可用候选后再测试 WRITE/PROMOTED",
        intermediate=endpoint_ready.format_table(),
        actual=snap_ready.format_record(candidate_node="test_mmr2", user_name="qa_app_user"),
        result="PASS",
    )

    promoted_before = conf.read_text(encoding="utf-8")
    set_promoted = ops.admin_psql("SET NODE PROMOTED test_mmr2 IN GROUP qa_mmr;")
    promoted_after = conf.read_text(encoding="utf-8")
    if 'promoted_cluster "site_b"' not in promoted_after:
        raise ConsoleAssertionError("SET NODE PROMOTED did not persist promoted_cluster site_b")
    snap_promoted = _wait_mmr_candidate(context, "test_mmr2")
    ops.add_step("SET NODE PROMOTED IN GROUP", command=ops.console_result(set_promoted),
                intermediate="B0 当前路由状态:\n%s\n\n配置持久化检查:\npromoted_cluster \"site_b\"\n配置变化: %s" % (
                    snap_promoted.format_record(candidate_node="test_mmr2"),
                    "无变化（目标已是 site_b，幂等执行）" if promoted_before == promoted_after else "已更新"),
                expected="命令成功，qa_mmr 持久化 promoted_cluster=site_b；正常态不要求 B0 effective_state=promoted",
                actual="返回码=0；配置包含 promoted_cluster \"site_b\"；B0 正常态保持 active", result="PASS")

    set_b0 = ops.admin_psql("SET NODE WRITE test_mmr2 IN GROUP qa_mmr;")
    snap_write = _wait_mmr_candidate(context, "test_mmr2", write_target=True)
    snap_write.assert_field({"candidate_node": "test_mmr2"}, "is_write_target", "true")
    snap_write.assert_fields({"candidate_node": "test_mmr2", "user_name": "qa_app_user"},
                             candidate_type="WRITE", write_source="WRITE_CLUSTER", route_status="AVAILABLE")
    snap_write.assert_write_target_count(1, user_name="qa_app_user")
    ops.add_step("SET NODE WRITE IN GROUP", command=ops.console_result(set_b0), intermediate=snap_write.format_record(candidate_node="test_mmr2"),
                expected="B0 成为唯一 write target", actual="test_mmr2 is_write_target=true", result="PASS")

    set_a = ops.admin_psql("SET NODE WRITE test_mmr1_s1 IN GROUP qa_mmr;")
    snap_site_a = ops.admin_psql("SHOW GROUP_ROUTING qa_mmr;")
    snap_site_a.assert_field({"candidate_node": "test_mmr1"}, "is_write_target", "true")
    snap_site_a.assert_fields({"candidate_node": "test_mmr1_s1", "user_name": "qa_app_user"},
                              candidate_type="READ", is_write_target="false")
    snap_site_a.assert_write_target_count(1, user_name="qa_app_user")
    snap_site_a.assert_fields({"candidate_node": "test_mmr1", "user_name": "qa_app_user"},
                              candidate_type="WRITE", write_source="WRITE_CLUSTER", route_status="AVAILABLE")
    ops.record_step(
        "通过节点设置集群写中心",
        ops.console_result(set_a),
        "定位所属集群 site_a 并将主库设为写中心",
        "test_mmr1 write_target=true",
        "PASS",
    )

    before_failed_commands = _digest(conf)
    failed_node = ops.admin_psql("SET NODE WRITE non_existent_node_xyz IN GROUP qa_mmr;", check=False)
    failed_node.assert_error("non_existent_node_xyz", "does not exist")
    if _digest(conf) != before_failed_commands:
        raise ConsoleAssertionError("Invalid node command changed configuration")
    ops.add_step("非法节点拒绝执行", command=ops.console_result(failed_node),
                expected="因节点不存在而明确拒绝", actual=failed_node.raw_output.strip(), result="PASS")

    failed_balance = ops.admin_psql("SET NODE WRITE test_mmr1 IN GROUP qa_bal_rw;", check=False)
    failed_balance.assert_error("qa_bal_rw", "not an MMR group")
    if _digest(conf) != before_failed_commands:
        raise ConsoleAssertionError("Balance WRITE command changed configuration")
    ops.add_step("非 MMR 组拒绝设写中心", command=ops.console_result(failed_balance),
                expected="因 qa_bal_rw 不是 MMR 组而明确拒绝", actual=failed_balance.raw_output.strip(), result="PASS")

    failed_group = ops.admin_psql("SET NODE WRITE test_mmr1 IN GROUP nonexistent_group_abc;", check=False)
    failed_group.assert_error("nonexistent_group_abc", "does not exist")
    if _digest(conf) != before_failed_commands:
        raise ConsoleAssertionError("Invalid group command changed configuration")
    ops.add_step("不存在的组拒绝执行", command=ops.console_result(failed_group),
                expected="因组不存在而明确拒绝", actual=failed_group.raw_output.strip(), result="PASS")

    refresh = ops.admin_psql("REFRESH CLUSTER site_a;")
    before_probe = ops.admin_psql("SHOW ENDPOINT_MONITOR test_mmr1_s1;")
    initial_seq = int(before_probe.find_one(node_name="test_mmr1_s1")["probe_seq"])
    after_probe = ops.console_wait(
        "SHOW ENDPOINT_MONITOR test_mmr1_s1;",
        lambda s: int(s.find_one(node_name="test_mmr1_s1")["probe_seq"]) > initial_seq,
        timeout=12, interval=0.25,
        describe="probe_seq growth after REFRESH")
    ops.add_step("REFRESH CLUSTER", command=ops.console_result(refresh),
                intermediate="刷新后探测序号确认:\n%s" % after_probe.format_table(),
                expected="命令返回成功且后续 probe_seq 增长", actual="probe_seq %d -> %s" % (
                    initial_seq, after_probe.find_one(node_name="test_mmr1_s1")["probe_seq"]), result="PASS")

    expected_schemas = {
        "SHOW DATASOURCES;": [
            "node_name", "cluster_name", "config_storage_db", "host", "port",
            "application_name", "system_identifier", "config_status", "config_check", "weight"
        ],
        "SHOW CLUSTERS;": [
            "cluster_name", "nodes", "monitor_enabled", "probe_features", "monitor_max_retries",
            "monitor_recovery_max_retries", "delay_threshold", "topology_state", "topology_seq",
            "current_primary", "last_trusted_primary", "route_publish_state", "route_publish_error"
        ],
        "SHOW GROUP_MEMBERS;": [
            "group_name", "database", "user", "group_mode", "rw_split_method", "node_name",
            "cluster_name", "storage_db", "storage_host", "storage_port", "weight",
            "effective_check", "config_status", "state", "group_role", "effective_grouprole", "primary"
        ],
        "SHOW GROUP_ROUTING;": [
            "group_name", "group_mode", "user_name", "cluster_name", "current_primary",
            "candidate_node", "candidate_type", "effective_grouprole", "effective_state",
            "is_write_target", "write_source", "fallback_reason", "route_status", "unavailable_reason"
        ],
        "SHOW CONFIG_STATUS;": [
            "config_generation", "last_config_publish_time", "last_reload_kind",
            "last_reload_result", "main_config_digest", "userlist_digest",
            "monitor_registry_generation", "parser_policy_version"
        ],
    }
    for cmd, expected_cols in expected_schemas.items():
        snap = ops.admin_psql(cmd)
        if not snap.records:
            raise ConsoleAssertionError("Command %s returned no rows!" % cmd)
        actual_cols = list(snap.records[0].keys())
        snap.assert_columns(*expected_cols)
        ops.add_step("SHOW 列名全量审计 %s" % cmd, command=ops.console_result(snap),
                    expected="包含设计列名: %s" % ", ".join(expected_cols),
                    actual="实际列名: %s" % ", ".join(actual_cols), result="PASS")


def run_core_21_reload_parameters_and_structure(context):
    """CORE-21: Reload no change, runtime parameters, and structural changes."""
    ops = context.ops
    conf = ops.start()
    pid_before = ops.pid_file.read_text(encoding="utf-8").strip()

    rc, out = ops.client_psql("SELECT 1;")
    snap_status1 = ops.admin_psql("SHOW CONFIG_STATUS;")
    gen1 = snap_status1.records[0].get("config_generation", "0")
    registry1 = snap_status1.records[0].get("monitor_registry_generation", "0")

    reload_no_change = ops.admin_psql("RELOAD;")
    rc2, out2 = ops.client_psql("SELECT 1;")
    if rc2 != 0 or out2 != "1":
        raise ConsoleAssertionError("Client query failed after no-change reload!")

    snap_status2 = ops.admin_psql("SHOW CONFIG_STATUS;")
    pid_after = ops.pid_file.read_text(encoding="utf-8").strip()
    if pid_after != pid_before:
        raise ConsoleAssertionError("No-change Reload replaced process: before=%s after=%s" % (pid_before, pid_after))
    last_res = snap_status2.records[0].get("last_reload_result", "")
    if last_res not in ("NO_CHANGE", "SUCCESS"):
        raise ConsoleAssertionError("Expected last_reload_result in ('NO_CHANGE', 'SUCCESS'), got %r" % last_res)
    snap_status2.assert_fields({}, config_generation=gen1, monitor_registry_generation=registry1)
    ops.add_step("无变化 Reload", command="%s\n\n客户端验证:\n%s\n返回码: %s\n输出: %s" % (
                    ops.console_result(reload_no_change), ops.last_client_cmd, rc2, out2),
                intermediate=snap_status2.format_record(),
                expected="PID 保持不变，last_reload_result 为 NO_CHANGE/SUCCESS，Reload 后客户端 SELECT 1 返回 1",
                actual="PID %s -> %s；last_reload_result=%s；客户端返回=%s" % (
                    pid_before, pid_after, last_res, out2), result="PASS")

    conf_text = conf.read_text(encoding="utf-8")
    conf_text_mod = re.sub(r'monitor_period\s+\d+', 'monitor_period 5', conf_text)
    conf.write_text(conf_text_mod, encoding="utf-8")

    param_diff = ops.diff_text(conf_text, conf_text_mod, "修改前配置", "monitor_period=5 配置")
    reload_param = ops.admin_psql("RELOAD;")
    snap_status3 = ops.admin_psql("SHOW CONFIG_STATUS;")
    last_res3 = snap_status3.records[0].get("last_reload_result", "")
    if last_res3 != "SUCCESS":
        raise ConsoleAssertionError("Expected last_reload_result=SUCCESS on param update, got %r" % last_res3)
    snap_status3.assert_fields({}, last_reload_kind="IN_PLACE", monitor_registry_generation=registry1)
    deadline = time.monotonic() + 22
    previous_seq = None
    previous_time = None
    probe_snapshots = []
    intervals = []
    while time.monotonic() < deadline and len(intervals) < 2:
        probe_snapshot = ops.admin_psql("SHOW ENDPOINT_MONITOR test_mmr1_s1;")
        probe = probe_snapshot.find_one(node_name="test_mmr1_s1")
        if probe.get("effective_probe_period") != "5000":
            time.sleep(0.25)
            continue
        probe_seq = int(probe.get("probe_seq", "0"))
        if previous_seq is None or probe_seq != previous_seq:
            observed_at = time.monotonic()
            if previous_time is not None:
                intervals.append(observed_at - previous_time)
            previous_seq = probe_seq
            previous_time = observed_at
            probe_snapshots.append(probe_snapshot.format_table())
        time.sleep(0.25)
    if len(intervals) != 2 or any(not 3.5 <= interval <= 8.0 for interval in intervals):
        raise ConsoleAssertionError(
            "monitor_period=5 did not produce two steady probes: %s" % intervals
        )
    ops.add_step("运行参数变更 Reload", command=ops.console_result(reload_param),
                intermediate="写入配置文件的变更:\n%s\n\nSHOW CONFIG_STATUS:\n%s\n\n"
                             "连续探测的 SHOW ENDPOINT_MONITOR:\n%s" % (
                                 param_diff, snap_status3.format_table(),
                                 "\n\n".join(probe_snapshots)),
                expected="monitor_period 从 2 改为 5 后 Reload 成功，稳态探测间隔约 5 秒",
                actual="last_reload_result=%s；effective_probe_period=5000ms；"
                       "连续探测间隔 %.2fs、%.2fs" %
                       (last_res3, intervals[0], intervals[1]), result="PASS")

    new_group_block = (
        '\ngroup "qa_temp_group" {\n'
        '    group_mode "replication"\n'
        '    storage_db "postgres"\n'
        '    backend_clusters "site_a"\n'
        '    check "auto"\n'
        '}\n'
    )
    conf_text_mod2 = conf_text_mod + new_group_block
    conf_text_mod2 = conf_text_mod2.replace('group_names "qa_rep,', 'group_names "qa_temp_group,qa_rep,')
    conf.write_text(conf_text_mod2, encoding="utf-8")
    structure_diff = ops.diff_text(conf_text_mod, conf_text_mod2, "参数变更配置", "新增 qa_temp_group 配置")
    reload_structure = ops.admin_psql("RELOAD;")
    snap_temp = ops.admin_psql("SHOW GROUP_ROUTING qa_temp_group;")
    if not snap_temp.records:
        raise ConsoleAssertionError("Newly added group qa_temp_group not found in SHOW GROUP_ROUTING!")
    snap_status4 = ops.admin_psql("SHOW CONFIG_STATUS;")
    gen4 = snap_status4.records[0].get("config_generation", "0")
    if int(gen4) <= int(gen1):
        raise ConsoleAssertionError("Expected config_generation to increment, before=%s after=%s" % (gen1, gen4))
    snap_status4.assert_fields({}, last_reload_kind="STRUCTURAL", last_reload_result="SUCCESS")
    if int(snap_status4.records[0]["monitor_registry_generation"]) <= int(registry1):
        raise ConsoleAssertionError("Structural reload did not rebuild monitor registry")
    for user in ("qa_app_user", "qa_hint_user", "postgres"):
        snap_temp.assert_fields({"user_name": user, "candidate_type": "WRITE"},
                                candidate_node="test_mmr1", route_status="AVAILABLE")
        snap_temp.assert_fields({"user_name": user, "candidate_type": "READ"},
                                candidate_node="test_mmr1_s1", route_status="AVAILABLE")
    ops.add_step("结构新增组 Reload", command=ops.console_result(reload_structure),
                intermediate="写入配置文件的变更:\n%s\n\n新增组路由:\n%s\n\n配置状态:\n%s" % (
                    structure_diff, snap_temp.format_table(), snap_status4.format_table()),
                expected="qa_temp_group 路由立即生效且 config_generation 自增",
                actual="config_generation %s -> %s；路由返回 %d 行" % (gen1, gen4, len(snap_temp.records)), result="PASS")

    conf.write_text(conf_text, encoding="utf-8")
    restore_diff = ops.diff_text(conf_text_mod2, conf_text, "测试配置", "原始配置")
    reload_restore = ops.admin_psql("RELOAD;")
    snap_removed = ops.admin_psql("SHOW GROUP_ROUTING qa_temp_group;", check=False)
    snap_removed.assert_error("qa_temp_group")
    ops.add_step("恢复配置结构 Reload", command="%s\n\n恢复后查询临时组:\n%s" % (
                    ops.console_result(reload_restore), ops.console_result(snap_removed)),
                intermediate="恢复配置文件的变更:\n%s" % restore_diff,
                expected="恢复原配置成功，qa_temp_group 不再存在",
                actual="Reload 返回码=0；临时组查询返回码=%s" % snap_removed.returncode, result="PASS")


def run_core_22_reload_failure_protection(context):
    """CORE-22: Reload syntax error protection and read-only persistence directory."""
    ops = context.ops
    conf = ops.start()
    before_status = ops.admin_psql("SHOW CONFIG_STATUS;")
    before_generation = before_status.records[0]["config_generation"]
    before_registry = before_status.records[0]["monitor_registry_generation"]

    original_text = conf.read_text(encoding="utf-8")
    broken_text = original_text + "\nthis_is_an_invalid_syntax_error_token_block {{{\n"
    conf.write_text(broken_text, encoding="utf-8")

    broken_diff = ops.diff_text(original_text, broken_text, "合法配置", "语法错误配置")
    reload_failure = ops.admin_psql("RELOAD;", check=False)
    if reload_failure.returncode == 0:
        raise ConsoleAssertionError("Invalid configuration RELOAD unexpectedly succeeded")
    reload_failure.assert_error("unknown parameter")

    snap_status = ops.admin_psql("SHOW CONFIG_STATUS;")
    last_res = snap_status.records[0].get("last_reload_result", "")
    if last_res != "FAILED":
        raise ConsoleAssertionError("Expected last_reload_result=FAILED, got %r" % last_res)
    snap_status.assert_fields({}, config_generation=before_generation,
                              monitor_registry_generation=before_registry)

    rc, out = ops.client_psql("SELECT 1;")
    if rc != 0 or out != "1":
        raise ConsoleAssertionError("Existing client traffic was broken by invalid config reload!")

    ops.add_step("配置语法错误 Reload 保护", command="%s\n\n客户端验证:\n%s\n返回码: %s\n输出: %s" % (
                    ops.console_result(reload_failure), ops.last_client_cmd, rc, out),
                intermediate="注入的配置变更:\n%s\n\nSHOW CONFIG_STATUS:\n%s" % (
                    broken_diff, snap_status.format_table()),
                expected="Reload 明确失败，last_reload_result=FAILED，旧配置继续服务",
                actual="Reload 返回码=%s；last_reload_result=%s；SELECT 1 返回 %s" % (
                    reload_failure.returncode, last_res, out), result="PASS")

    conf.write_text(original_text, encoding="utf-8")
    restore_reload = ops.admin_psql("RELOAD;")

    backup_dir = ops.backup_dir
    before_persist = conf.read_text(encoding="utf-8")
    before_members = ops.admin_psql("SHOW GROUP_MEMBERS;")
    os.chmod(str(backup_dir), 0o555)
    try:
        write_failure = ops.admin_psql("SET NODE PARTED test_mmr1_s1;", check=False)
    finally:
        os.chmod(str(backup_dir), 0o755)
    if write_failure.returncode == 0:
        raise ConsoleAssertionError("SET NODE unexpectedly succeeded with read-only backup directory")
    write_failure.assert_error("cannot lock configuration file", "permission denied")

    after_persist = conf.read_text(encoding="utf-8")
    if before_persist != after_persist:
        raise ConsoleAssertionError("Configuration changed after persistence failure")
    after_members = ops.admin_psql("SHOW GROUP_MEMBERS;")
    before_row = before_members.find_one(node_name="test_mmr1_s1", group_name="qa_rep")
    after_row = after_members.find_one(node_name="test_mmr1_s1", group_name="qa_rep")
    if before_row.get("state") != after_row.get("state"):
        raise ConsoleAssertionError("Runtime state changed after persistence failure")
    for field in ("state", "config_status", "group_role", "effective_grouprole"):
        if before_row.get(field) != after_row.get(field):
            raise ConsoleAssertionError("Persistence failure changed member field %s" % field)

    ops.add_step("恢复合法配置", command=ops.console_result(restore_reload),
                expected="恢复原始配置并 Reload 成功", actual="返回码=0", result="PASS")
    ops.add_step("只读持久化目录写保护", command="chmod 555 %s\n\n%s\n\nchmod 755 %s" % (
                    backup_dir, ops.console_result(write_failure), backup_dir),
                intermediate="操作前成员:\n%s\n\n操作后成员:\n%s\n\n配置文件 diff:\n<无差异>" % (
                    before_members.format_record(node_name="test_mmr1_s1", group_name="qa_rep"),
                    after_members.format_record(node_name="test_mmr1_s1", group_name="qa_rep")),
                expected="持久化受阻时命令明确失败，内存状态与磁盘配置均不变化，目录权限恢复",
                actual="返回码=%s；成员 state 保持 %s；配置逐字节一致；权限已恢复为 755" % (
                    write_failure.returncode, after_row.get("state")), result="PASS")

    invalid_group = (
        '\ngroup "qa_invalid_cluster" {\n'
        '    group_mode "single"\n'
        '    storage_db "postgres"\n'
        '    access_mode "read_write"\n'
        '    backend_clusters "site_missing_manual"\n'
        '    check "auto"\n'
        '}\n'
    )
    original_digest = _digest(conf)
    conf.write_text(original_text + invalid_group, encoding="utf-8")
    try:
        invalid_reload = ops.admin_psql("RELOAD;", check=False)
        invalid_reload.assert_error("rules validate")
        invalid_status = ops.admin_psql("SHOW CONFIG_STATUS;")
        invalid_status.assert_fields({}, last_reload_result="FAILED",
                                     config_generation=before_generation,
                                     monitor_registry_generation=before_registry)
        old_route = _wait_rep_route_ready(context)
        old_route.assert_fields({"user_name": "qa_hint_user", "candidate_type": "WRITE"},
                                candidate_node="test_mmr1", route_status="AVAILABLE")
        rc, out = ops.client_psql("SELECT 1;", db="qa_rep")
        if rc != 0 or out != "1":
            raise ConsoleAssertionError("Old route failed after invalid cluster reload")
        client_cmd = ops.last_client_cmd
    finally:
        conf.write_text(original_text, encoding="utf-8")
    if _digest(conf) != original_digest:
        raise ConsoleAssertionError("Configuration digest not restored after invalid cluster test")
    ops.add_step("未知 cluster 的合法语法 Reload 拒绝", command="%s\n\n%s\n%s" % (
        ops.console_result(invalid_reload), client_cmd, out),
        intermediate="%s\n\n%s" % (invalid_status.format_table(), old_route.format_table()),
        evidence="\n".join(ops.extract_log_lines(["rules validate", "reload failed"], max_lines=6)),
        expected="规则校验失败，代次不变，旧路由继续服务且配置恢复原摘要",
        actual="last_reload_result=FAILED；generation=%s；registry=%s；SELECT 1=%s；SHA-256=%s" % (
            before_generation, before_registry, out, original_digest), result="PASS")

    recovered_reload = ops.admin_psql("RELOAD;")
    final_status = ops.admin_psql("SHOW CONFIG_STATUS;")
    final_status.assert_fields({}, config_generation=before_generation,
                               monitor_registry_generation=before_registry)
    final_route = _wait_rep_route_ready(context)
    ops.add_step("失败条件解除后恢复核查", command=ops.console_result(recovered_reload),
                intermediate="%s\n\n%s" % (final_status.format_table(), final_route.format_table()),
                expected="恢复原配置后 Reload 和 qa_rep 路由正常", actual="代次未增加；A0 WRITE、A1 READ 均 AVAILABLE",
                result="PASS")
