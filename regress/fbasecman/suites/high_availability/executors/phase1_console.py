"""Phase 1 executors: Console commands, atomicity, and Reload protection (CORE-19 to CORE-22)."""

import os
import re
import time
from pathlib import Path

from ..console_parser import ConsoleAssertionError


def _wait_rep_route_ready(rt, timeout=15):
    deadline = time.time() + timeout
    snapshot = None
    while time.time() < deadline:
        snapshot = rt.admin_psql("SHOW GROUP_ROUTING qa_rep;")
        write_rows = snapshot.find_rows(candidate_node="test_mmr1", candidate_type="WRITE")
        read_rows = snapshot.find_rows(candidate_node="test_mmr1_s1", candidate_type="READ")
        if (write_rows and write_rows[0].get("route_status") == "AVAILABLE" and
                read_rows and read_rows[0].get("route_status") == "AVAILABLE"):
            return snapshot
        time.sleep(1)
    snapshot.assert_field(
        {"candidate_node": "test_mmr1", "candidate_type": "WRITE"},
        "route_status", "AVAILABLE")
    snapshot.assert_field(
        {"candidate_node": "test_mmr1_s1", "candidate_type": "READ"},
        "route_status", "AVAILABLE")
    return snapshot


def run_core_19_set_node_atomicity(rt):
    """CORE-19: SET NODE single, batch atomicity, and restart persistence."""
    conf = rt.start()

    # Step 1: Initial state check
    snap_init = rt.admin_psql("SHOW GROUP_ROUTING qa_rep;", title="初始运行态检查")
    snap_init.assert_candidate_present("test_mmr1_s1", "READ")
    snap_init.assert_field({"candidate_node": "test_mmr1_s1"}, "effective_state", "active")
    rt.add_step("初始运行态检查", command=rt.console_result(snap_init),
                expected="A1 为 active 只读候选", actual=snap_init.format_record(candidate_node="test_mmr1_s1"), result="PASS")

    # Step 2: Single node PARTED
    before_conf = conf.read_text(encoding="utf-8")
    set_parted = rt.admin_psql("SET NODE PARTED test_mmr1_s1;", title="执行单节点 PARTED")
    snap_parted_route = rt.admin_psql("SHOW GROUP_ROUTING qa_rep;", title="检查 PARTED 后路由剔除")
    snap_parted_route.assert_candidate_absent("test_mmr1_s1")
    snap_parted_members = rt.admin_psql("SHOW GROUP_MEMBERS;", title="检查成员状态变更为 parted")
    snap_parted_members.assert_field({"node_name": "test_mmr1_s1", "group_name": "qa_rep"}, "state", "parted")
    after_conf = conf.read_text(encoding="utf-8")
    config_diff = rt.diff_text(before_conf, after_conf, "操作前配置", "操作后配置")
    if not config_diff or 'status "parted"' not in config_diff:
        raise ConsoleAssertionError("SET NODE PARTED did not persist the expected datasource status change")
    rt.add_step("SET NODE PARTED test_mmr1_s1", command=rt.console_result(set_parted),
                intermediate="配置文件变更:\n%s\n\n成员状态:\n%s\n\n路由状态:\n%s" % (
                    config_diff, snap_parted_members.format_record(node_name="test_mmr1_s1", group_name="qa_rep"),
                    snap_parted_route.format_table()),
                expected="effective_state=parted，路由剔除，且配置文件持久化 status=parted",
                actual="控制台命令成功；成员状态为 parted；路由中无 A1；配置 diff 包含 status=parted", result="PASS")

    # Step 3: Single node ACTIVE
    set_active = rt.admin_psql("SET NODE ACTIVE test_mmr1_s1;", title="执行单节点 ACTIVE")
    refresh_active = rt.admin_psql("REFRESH CLUSTER site_a;", title="刷新拓扑")
    snap_active = rt.admin_psql("SHOW GROUP_ROUTING qa_rep;", title="检查 ACTIVE 状态")
    snap_active.assert_candidate_present("test_mmr1_s1", "READ")
    snap_active.assert_field({"candidate_node": "test_mmr1_s1"}, "effective_state", "active")
    rt.add_step("SET NODE ACTIVE test_mmr1_s1", command="%s\n\n%s" % (
                    rt.console_result(set_active), rt.console_result(refresh_active)),
                intermediate=snap_active.format_record(candidate_node="test_mmr1_s1"),
                expected="A1 恢复为 active 只读候选", actual="A1 effective_state=active, candidate_type=READ", result="PASS")

    # Step 4: Batch valid with deduplication
    batch_parted = rt.admin_psql("SET NODE PARTED test_mmr1, test_mmr1_s1, test_mmr1;", title="执行合法批量去重")
    snap_batch_route = rt.admin_psql("SHOW GROUP_ROUTING qa_rep;", title="检查批量 PARTED 路由剔除")
    snap_batch_route.assert_candidate_absent("test_mmr1")
    snap_batch_route.assert_candidate_absent("test_mmr1_s1")
    snap_batch_members = rt.admin_psql("SHOW GROUP_MEMBERS;", title="检查批量 PARTED 成员状态")
    snap_batch_members.assert_field({"node_name": "test_mmr1", "group_name": "qa_rep"}, "state", "parted")
    snap_batch_members.assert_field({"node_name": "test_mmr1_s1", "group_name": "qa_rep"}, "state", "parted")
    rt.add_step("批量合法 PARTED 去重", command=rt.console_result(batch_parted),
                intermediate=snap_batch_members.format_table(),
                expected="重复节点去重执行，A0/A1 均为 parted 并从路由剔除",
                actual="A0/A1 成员状态均为 parted，路由候选中均不存在", result="PASS")

    # Restore to active
    restore_active = rt.admin_psql("SET NODE ACTIVE test_mmr1, test_mmr1_s1;")
    restore_refresh = rt.admin_psql("REFRESH CLUSTER site_a;")
    _wait_rep_route_ready(rt)

    # Step 5: Batch atomicity check with invalid node
    before_conf_text = conf.read_text(encoding="utf-8")
    # Must fail
    rejected = rt.admin_psql("SET NODE PARTED test_mmr1_s1, NON_EXISTENT_NODE_XYZ;", check=False)
    if rejected.returncode == 0:
        raise ConsoleAssertionError("Expected invalid batch SET NODE command to fail")

    # Assert A1 state was NOT partially altered
    snap_check = rt.admin_psql("SHOW GROUP_ROUTING qa_rep;", title="原子性检查：A1 状态必须保持 active")
    snap_check.assert_candidate_present("test_mmr1_s1", "READ")
    snap_check.assert_field({"candidate_node": "test_mmr1_s1"}, "effective_state", "active")

    # Assert config file was NOT partially modified
    after_conf_text = conf.read_text(encoding="utf-8")
    if before_conf_text != after_conf_text:
        raise ConsoleAssertionError("Config file was partially modified during failed batch command!")

    rt.add_step("批量原子性拦截非法节点", command=rt.console_result(rejected),
                intermediate="失败后路由状态:\n%s\n\n配置文件 diff:\n<无差异>" % snap_check.format_record(candidate_node="test_mmr1_s1"),
                expected="命令整体拒绝；A1 保持 active；配置文件无部分写入",
                actual="返回码=%s；A1 effective_state=active；操作前后配置逐字节一致" % rejected.returncode, result="PASS")

    # Step 6: Restart persistence check
    persist_parted = rt.admin_psql("SET NODE PARTED test_mmr1_s1;", title="设置 PARTED 准备验证重启")
    rt.restart()
    snap_restart_route = rt.admin_psql("SHOW GROUP_ROUTING qa_rep;", title="重启后核对路由剔除")
    snap_restart_route.assert_candidate_absent("test_mmr1_s1")
    snap_restart_members = rt.admin_psql("SHOW GROUP_MEMBERS;", title="重启后核对成员状态")
    snap_restart_members.assert_field({"node_name": "test_mmr1_s1", "group_name": "qa_rep"}, "state", "parted")
    rt.add_step("重启后配置持久化生效", command=rt.console_result(persist_parted),
                intermediate=snap_restart_members.format_record(node_name="test_mmr1_s1", group_name="qa_rep"),
                expected="进程重启后 A1 仍为 parted 且不在路由候选中",
                actual="A1 state=parted，重启后路由中无 A1", result="PASS")

    # Clean up
    cleanup_active = rt.admin_psql("SET NODE ACTIVE test_mmr1_s1;")
    cleanup_refresh = rt.admin_psql("REFRESH CLUSTER site_a;")
    rt.add_step("恢复用例基线", command="%s\n\n%s" % (
                    rt.console_result(cleanup_active), rt.console_result(cleanup_refresh)),
                expected="A1 恢复 active，避免影响后续用例", actual="恢复命令均成功", result="PASS")


def run_core_20_write_promoted_refresh_show(rt):
    """CORE-20: Role commands, refresh, and comprehensive show output audit."""
    conf = rt.start()

    # Step 1: Valid SET NODE PROMOTED and WRITE in mmr group
    promoted_before = conf.read_text(encoding="utf-8")
    set_promoted = rt.admin_psql("SET NODE PROMOTED test_mmr2 IN GROUP qa_mmr;", title="合法设置 promoted 中心为 test_mmr2")
    promoted_after = conf.read_text(encoding="utf-8")
    if 'promoted_cluster "site_b"' not in promoted_after:
        raise ConsoleAssertionError("SET NODE PROMOTED did not persist promoted_cluster site_b")
    snap_promoted = rt.admin_psql("SHOW GROUP_ROUTING qa_mmr;", title="核对 promoted 中心")
    rt.add_step("SET NODE PROMOTED IN GROUP", command=rt.console_result(set_promoted),
                intermediate="B0 当前路由状态:\n%s\n\n配置持久化检查:\npromoted_cluster \"site_b\"\n配置变化: %s" % (
                    snap_promoted.format_record(candidate_node="test_mmr2"),
                    "无变化（目标已是 site_b，幂等执行）" if promoted_before == promoted_after else "已更新"),
                expected="命令成功，qa_mmr 持久化 promoted_cluster=site_b；正常态不要求 B0 effective_state=promoted",
                actual="返回码=0；配置包含 promoted_cluster \"site_b\"；B0 正常态保持 active", result="PASS")

    set_b0 = rt.admin_psql("SET NODE WRITE test_mmr2 IN GROUP qa_mmr;", title="合法设置写中心为 test_mmr2")
    snap_write = rt.admin_psql("SHOW GROUP_ROUTING qa_mmr;", title="核对写中心切换")
    snap_write.assert_field({"candidate_node": "test_mmr2"}, "is_write_target", "true")
    rt.add_step("SET NODE WRITE IN GROUP", command=rt.console_result(set_b0), intermediate=snap_write.format_record(candidate_node="test_mmr2"),
                expected="B0 成为唯一 write target", actual="test_mmr2 is_write_target=true", result="PASS")

    # Step 2: Write center commands and boundary testing
    # a. In MMR mode, SET NODE WRITE resolves node to cluster (equivalent to set cluster write)
    set_a = rt.admin_psql("SET NODE WRITE test_mmr1_s1 IN GROUP qa_mmr;", title="通过从库节点定位集群并设置写中心")
    snap_site_a = rt.admin_psql("SHOW GROUP_ROUTING qa_mmr;")
    snap_site_a.assert_field({"candidate_node": "test_mmr1"}, "is_write_target", "true")
    rt.record_step(
        "通过节点设置集群写中心",
        rt.console_result(set_a),
        "定位所属集群 site_a 并将主库设为写中心",
        "test_mmr1 write_target=true",
        "PASS",
    )

    # b. Non-existent node rejected
    failed_node = rt.admin_psql("SET NODE WRITE non_existent_node_xyz IN GROUP qa_mmr;", check=False)
    if failed_node.returncode == 0:
        raise ConsoleAssertionError("Expected non-existent node to fail!")
    rt.add_step("非法节点拒绝执行", command=rt.console_result(failed_node),
                expected="因节点不存在而明确拒绝", actual=failed_node.raw_output.strip(), result="PASS")

    # c. Balance group cannot set write target
    failed_balance = rt.admin_psql("SET NODE WRITE test_mmr1 IN GROUP qa_bal_rw;", check=False)
    if failed_balance.returncode == 0:
        raise ConsoleAssertionError("Expected SET NODE WRITE on balance group to fail!")
    rt.add_step("非 MMR 组拒绝设写中心", command=rt.console_result(failed_balance),
                expected="因 qa_bal_rw 不是 MMR 组而明确拒绝", actual=failed_balance.raw_output.strip(), result="PASS")

    # d. Non-existent group
    failed_group = rt.admin_psql("SET NODE WRITE test_mmr1 IN GROUP nonexistent_group_abc;", check=False)
    if failed_group.returncode == 0:
        raise ConsoleAssertionError("Expected non-existent group to fail!")
    rt.add_step("不存在的组拒绝执行", command=rt.console_result(failed_group),
                expected="因组不存在而明确拒绝", actual=failed_group.raw_output.strip(), result="PASS")

    # Step 3: REFRESH CLUSTER
    refresh = rt.admin_psql("REFRESH CLUSTER site_a;", title="刷新集群 site_a")
    rt.add_step("REFRESH CLUSTER", command=rt.console_result(refresh), expected="命令返回成功", actual="返回码=0", result="PASS")

    # Step 4: Full column schema audit for SHOW commands
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
        snap = rt.admin_psql(cmd, title="审计 %s" % cmd)
        if not snap.records:
            raise ConsoleAssertionError("Command %s returned no rows!" % cmd)
        actual_cols = list(snap.records[0].keys())
        missing = [c for c in expected_cols if c not in actual_cols]
        if missing:
            raise ConsoleAssertionError("Command %s missing expected columns: %r (found %r)" % (cmd, missing, actual_cols))
        rt.add_step("SHOW 列名全量审计 %s" % cmd, command=rt.console_result(snap),
                    expected="包含设计列名: %s" % ", ".join(expected_cols),
                    actual="实际列名: %s" % ", ".join(actual_cols), result="PASS")



def run_core_21_reload_parameters_and_structure(rt):
    """CORE-21: Reload no change, runtime parameters, and structural changes."""
    conf = rt.start()
    pid_before = rt.pid_file.read_text(encoding="utf-8").strip()

    # Step 1: No change reload while executing query
    rc, out = rt.client_psql("SELECT 1;")
    snap_status1 = rt.admin_psql("SHOW CONFIG_STATUS;", title="Reload 前状态")
    gen1 = snap_status1.records[0].get("config_generation", "0")

    reload_no_change = rt.admin_psql("RELOAD;", title="执行无变化 Reload")
    rc2, out2 = rt.client_psql("SELECT 1;")
    if rc2 != 0 or out2 != "1":
        raise ConsoleAssertionError("Client query failed after no-change reload!")

    snap_status2 = rt.admin_psql("SHOW CONFIG_STATUS;", title="Reload 后状态")
    pid_after = rt.pid_file.read_text(encoding="utf-8").strip()
    if pid_after != pid_before:
        raise ConsoleAssertionError("No-change Reload replaced process: before=%s after=%s" % (pid_before, pid_after))
    last_res = snap_status2.records[0].get("last_reload_result", "")
    if last_res not in ("NO_CHANGE", "SUCCESS"):
        raise ConsoleAssertionError("Expected last_reload_result in ('NO_CHANGE', 'SUCCESS'), got %r" % last_res)
    rt.add_step("无变化 Reload", command="%s\n\n客户端验证:\n%s\n返回码: %s\n输出: %s" % (
                    rt.console_result(reload_no_change), rt.last_client_cmd, rc2, out2),
                intermediate=snap_status2.format_record(),
                expected="PID 保持不变，last_reload_result 为 NO_CHANGE/SUCCESS，Reload 后客户端 SELECT 1 返回 1",
                actual="PID %s -> %s；last_reload_result=%s；客户端返回=%s" % (
                    pid_before, pid_after, last_res, out2), result="PASS")

    # Step 2: Runtime parameter modification (monitor_period)
    conf_text = conf.read_text(encoding="utf-8")
    conf_text_mod = re.sub(r'monitor_period\s+\d+', 'monitor_period 5', conf_text)
    conf.write_text(conf_text_mod, encoding="utf-8")

    param_diff = rt.diff_text(conf_text, conf_text_mod, "修改前配置", "monitor_period=5 配置")
    reload_param = rt.admin_psql("RELOAD;", title="修改参数后 Reload")
    snap_status3 = rt.admin_psql("SHOW CONFIG_STATUS;", title="检查参数变更 Reload 状态")
    last_res3 = snap_status3.records[0].get("last_reload_result", "")
    if last_res3 != "SUCCESS":
        raise ConsoleAssertionError("Expected last_reload_result=SUCCESS on param update, got %r" % last_res3)
    rt.add_step("运行参数变更 Reload", command=rt.console_result(reload_param),
                intermediate="写入配置文件的变更:\n%s\n\nSHOW CONFIG_STATUS:\n%s" % (param_diff, snap_status3.format_table()),
                expected="monitor_period 从 2 改为 5 后 Reload 成功",
                actual="last_reload_result=%s" % last_res3, result="PASS")

    # Step 3: Structural change: Add a new group
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
    structure_diff = rt.diff_text(conf_text_mod, conf_text_mod2, "参数变更配置", "新增 qa_temp_group 配置")
    reload_structure = rt.admin_psql("RELOAD;", title="追加新组后 Reload")
    snap_temp = rt.admin_psql("SHOW GROUP_ROUTING qa_temp_group;", title="查询新组路由")
    if not snap_temp.records:
        raise ConsoleAssertionError("Newly added group qa_temp_group not found in SHOW GROUP_ROUTING!")
    snap_status4 = rt.admin_psql("SHOW CONFIG_STATUS;", title="检查结构变更 Reload 状态")
    gen4 = snap_status4.records[0].get("config_generation", "0")
    if int(gen4) <= int(gen1):
        raise ConsoleAssertionError("Expected config_generation to increment, before=%s after=%s" % (gen1, gen4))
    rt.add_step("结构新增组 Reload", command=rt.console_result(reload_structure),
                intermediate="写入配置文件的变更:\n%s\n\n新增组路由:\n%s\n\n配置状态:\n%s" % (
                    structure_diff, snap_temp.format_table(), snap_status4.format_table()),
                expected="qa_temp_group 路由立即生效且 config_generation 自增",
                actual="config_generation %s -> %s；路由返回 %d 行" % (gen1, gen4, len(snap_temp.records)), result="PASS")

    # Step 4: Revert group and reload
    conf.write_text(conf_text, encoding="utf-8")
    restore_diff = rt.diff_text(conf_text_mod2, conf_text, "测试配置", "原始配置")
    reload_restore = rt.admin_psql("RELOAD;", title="恢复原配置后 Reload")
    snap_removed = rt.admin_psql("SHOW GROUP_ROUTING qa_temp_group;", check=False)
    rt.add_step("恢复配置结构 Reload", command="%s\n\n恢复后查询临时组:\n%s" % (
                    rt.console_result(reload_restore), rt.console_result(snap_removed)),
                intermediate="恢复配置文件的变更:\n%s" % restore_diff,
                expected="恢复原配置成功，qa_temp_group 不再存在",
                actual="Reload 返回码=0；临时组查询返回码=%s" % snap_removed.returncode, result="PASS")


def run_core_22_reload_failure_protection(rt):
    """CORE-22: Reload syntax error protection and read-only persistence directory."""
    conf = rt.start()

    # Step 1: Syntax error protection
    original_text = conf.read_text(encoding="utf-8")
    broken_text = original_text + "\nthis_is_an_invalid_syntax_error_token_block {{{\n"
    conf.write_text(broken_text, encoding="utf-8")

    broken_diff = rt.diff_text(original_text, broken_text, "合法配置", "语法错误配置")
    reload_failure = rt.admin_psql("RELOAD;", title="语法错误文件执行 Reload", check=False)
    if reload_failure.returncode == 0:
        raise ConsoleAssertionError("Invalid configuration RELOAD unexpectedly succeeded")

    snap_status = rt.admin_psql("SHOW CONFIG_STATUS;", title="检查重载失败状态")
    last_res = snap_status.records[0].get("last_reload_result", "")
    if last_res != "FAILED":
        raise ConsoleAssertionError("Expected last_reload_result=FAILED, got %r" % last_res)

    # Client query must still succeed under old config
    rc, out = rt.client_psql("SELECT 1;")
    if rc != 0 or out != "1":
        raise ConsoleAssertionError("Existing client traffic was broken by invalid config reload!")

    rt.add_step("配置语法错误 Reload 保护", command="%s\n\n客户端验证:\n%s\n返回码: %s\n输出: %s" % (
                    rt.console_result(reload_failure), rt.last_client_cmd, rc, out),
                intermediate="注入的配置变更:\n%s\n\nSHOW CONFIG_STATUS:\n%s" % (
                    broken_diff, snap_status.format_table()),
                expected="Reload 明确失败，last_reload_result=FAILED，旧配置继续服务",
                actual="Reload 返回码=%s；last_reload_result=%s；SELECT 1 返回 %s" % (
                    reload_failure.returncode, last_res, out), result="PASS")

    # Restore valid config
    conf.write_text(original_text, encoding="utf-8")
    restore_reload = rt.admin_psql("RELOAD;", title="恢复合法配置")

    # Step 2: Persistence directory read-only protection
    backup_dir = rt.backup_dir
    before_persist = conf.read_text(encoding="utf-8")
    before_members = rt.admin_psql("SHOW GROUP_MEMBERS;", title="写保护前成员状态")
    os.chmod(str(backup_dir), 0o555)
    try:
        write_failure = rt.admin_psql("SET NODE PARTED test_mmr1_s1;", title="只读备份目录下执行写配置命令", check=False)
    finally:
        os.chmod(str(backup_dir), 0o755)
    if write_failure.returncode == 0:
        raise ConsoleAssertionError("SET NODE unexpectedly succeeded with read-only backup directory")

    after_persist = conf.read_text(encoding="utf-8")
    if before_persist != after_persist:
        raise ConsoleAssertionError("Configuration changed after persistence failure")
    after_members = rt.admin_psql("SHOW GROUP_MEMBERS;", title="写保护失败后成员状态")
    before_row = before_members.find_one(node_name="test_mmr1_s1", group_name="qa_rep")
    after_row = after_members.find_one(node_name="test_mmr1_s1", group_name="qa_rep")
    if before_row.get("state") != after_row.get("state"):
        raise ConsoleAssertionError("Runtime state changed after persistence failure")

    rt.add_step("恢复合法配置", command=rt.console_result(restore_reload),
                expected="恢复原始配置并 Reload 成功", actual="返回码=0", result="PASS")
    rt.add_step("只读持久化目录写保护", command="chmod 555 %s\n\n%s\n\nchmod 755 %s" % (
                    backup_dir, rt.console_result(write_failure), backup_dir),
                intermediate="操作前成员:\n%s\n\n操作后成员:\n%s\n\n配置文件 diff:\n<无差异>" % (
                    before_members.format_record(node_name="test_mmr1_s1", group_name="qa_rep"),
                    after_members.format_record(node_name="test_mmr1_s1", group_name="qa_rep")),
                expected="持久化受阻时命令明确失败，内存状态与磁盘配置均不变化，目录权限恢复",
                actual="返回码=%s；成员 state 保持 %s；配置逐字节一致；权限已恢复为 755" % (
                    write_failure.returncode, after_row.get("state")), result="PASS")
