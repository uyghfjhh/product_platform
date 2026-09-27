import datetime
import io
import re
import subprocess
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from framework.catalog import (long_time_cases_for_plugins, render_case_details,
                               resolve_target, select_cases, select_cases_for_plugins,
                               validate_catalog)
from framework.discovery import discover_cases
from framework.assertions import command_succeeds, evaluate, evaluate_step, sql_fails
from framework.models import CaseResult, StepResult
from framework.reporting import _command_sql, render_case_report
from framework.runner import SuiteRunner
from suites.mac.suite import SUITE
from suites.mmr.suite import SUITE as MMR_SUITE


CASES = SUITE["cases"]
SSO_SYSTEM_PRIVILEGES = next(
    case for case in CASES
    if case["id"] == "mac.separation_of_duties.sso_system_privileges")


class CatalogTest(unittest.TestCase):
    def test_catalog_structure_is_valid(self):
        self.assertIsNone(validate_catalog())

    def test_suite_cases_are_discovered_from_case_modules(self):
        discovered = discover_cases("suites.mac.cases")
        self.assertEqual([case["id"] for case in discovered],
                         [case["id"] for case in CASES])

    def test_reported_psql_commands_declare_sql_display(self):
        self.maxDiff = None
        offenders = []
        for suite in (SUITE, MMR_SUITE):
            for case in suite["cases"]:
                for step in case["steps"]:
                    if step.get("type") != "command" or not step.get("report", True):
                        continue
                    command = " ".join(str(item) for item in step.get("argv") or [])
                    if ("psql" in command and not step.get("display_sql") and
                            not _command_sql(step)):
                        offenders.append("%s: %s" % (case["id"], step["title"]))
        self.assertEqual([], offenders)

    def test_mmr_report_text_uses_document_node_names(self):
        self.maxDiff = None
        forbidden = ("源节点", "目标节点", "source/", "target/", "forward1", "forward2")
        offenders = []
        for case in MMR_SUITE["cases"]:
            visible = [case["name"], *case.get("prerequisites", []), case["teardown"]]
            visible.extend(step["title"] for step in case["steps"]
                           if step.get("report", True))
            topology = case.get("test_topology") or {}
            visible.append(topology.get("summary", ""))
            visible.extend(item.get("name", "") for item in topology.get("nodes", []))
            visible.extend(topology.get("relations", []))
            for text in visible:
                if any(token in text for token in forbidden):
                    offenders.append("%s: %s" % (case["id"], text))
        self.assertEqual([], offenders)

    def test_target_prefix_selects_all_group_cases(self):
        selected = select_cases("mac.separation_of_duties")
        expected = [case["id"] for case in CASES
                    if case["group"] == "separation_of_duties"]
        self.assertEqual([case["id"] for case in selected], expected)

    def test_plugin_selection_selects_all_compatible_suite_cases(self):
        suites = {
            "mac": {"required_plugins": ["fbase_mac"],
                    "cases": [{"id": "mac.case"}]},
            "mmr": {"required_plugins": ["fdd_mmr"],
                    "cases": [{"id": "mmr.case"}]},
        }
        with patch("framework.catalog.SUITES", suites):
            selected = select_cases_for_plugins(["fbase_mac", "fdd_mmr"])
        self.assertEqual([case["id"] for case in selected],
                         ["mac.case", "mmr.case"])

    def test_default_selection_skips_long_manual_case_but_explicit_target_keeps_it(self):
        suites = {
            "mmr": {"required_plugins": ["fdd_mmr"], "groups": ["node_management"],
                    "cases": [{"id": "mmr.fast", "group": "node_management"},
                              {"id": "mmr.long", "group": "node_management",
                               "default_enabled": False}]},
        }
        with patch("framework.catalog.SUITES", suites):
            selected = select_cases_for_plugins(["fdd_mmr"])
            all_selected = select_cases_for_plugins(["fdd_mmr"], include_all=True)
            explicit = select_cases("mmr.long")
        self.assertEqual([case["id"] for case in selected], ["mmr.fast"])
        self.assertEqual([case["id"] for case in all_selected],
                         ["mmr.fast", "mmr.long"])
        self.assertEqual([case["id"] for case in explicit], ["mmr.long"])

    def test_longtime_selection_uses_enabled_plugin_suites_only(self):
        suites = {
            "mac": {"required_plugins": ["fbase_mac"], "cases": [
                {"id": "mac.long", "name": "[LONG-TIME] mac"}]},
            "mmr": {"required_plugins": ["fdd_mmr"], "cases": [
                {"id": "mmr.fast", "name": "fast"},
                {"id": "mmr.long", "name": "[LONG-TIME] mmr"}]},
        }
        with patch("framework.catalog.SUITES", suites):
            selected = long_time_cases_for_plugins(["fdd_mmr"])
        self.assertEqual([case["id"] for case in selected], ["mmr.long"])

    def test_plugin_selection_rejects_unregistered_plugin_suite(self):
        with patch("framework.catalog.SUITES", {"mac": {"cases": []}}):
            with self.assertRaisesRegex(Exception, "mmr"):
                select_cases_for_plugins(["fdd_mmr"])

    def test_short_case_name_resolves_to_full_target(self):
        self.assertEqual(
            resolve_target("sso_system_privileges", "mac"),
            "mac.separation_of_duties.sso_system_privileges",
        )

    def test_unique_partial_case_name_resolves_to_full_target(self):
        self.assertEqual(
            resolve_target("sso_system", "mac"),
            "mac.separation_of_duties.sso_system_privileges",
        )

    def test_show_contains_source_sql_and_expected_result(self):
        rendered = render_case_details(SSO_SYSTEM_PRIVILEGES["id"])
        self.assertIn("三权分立功能转测.md / 5.1.1", rendered)
        self.assertIn("ALTER USER sso LOGIN", rendered)
        self.assertIn("SQLSTATE=42501", rendered)

    def test_role_audit_case_uses_unique_audit_object_names(self):
        case = next(case for case in CASES
                    if case["id"] == "mac.audit.role_audit_logs")
        sql = "\n".join(step.get("sql", "") for step in case["steps"])
        self.assertRegex(sql, r"fbase_regress_audit_role_u1_[0-9a-f]{12}")
        self.assertRegex(sql, r"fbase_regress_audit_role_u2_[0-9a-f]{12}")

    def test_sm3_initdb_uses_password_file_without_terminal_prompt(self):
        case = next(case for case in CASES
                    if case["id"] == "mac.gm.sm3_authentication")
        commands = "\n".join(" ".join(step.get("argv", []))
                             for step in case["steps"])
        self.assertIn("--pwfile=/tmp/fbase_regress_sm3_auth_{run_id}/initdb_password", commands)
        self.assertNotIn(" -W", commands)

    def test_gb18030_english_or_query_uses_simple_configuration(self):
        case = next(case for case in CASES
                    if case["id"] == "mac.gb18030.full_text_search")
        step = next(step for step in case["steps"]
                    if step["title"] == "OR 组合检索中文和英文 people")
        self.assertNotIn("known_issue", case)
        self.assertIn("to_tsquery('simple', 'people')", step["sql"])

    def test_sm4_manual_tde_starts_use_a_pseudo_terminal(self):
        case = next(case for case in CASES
                    if case["id"] == "mac.gm.sm4_tde_lifecycle")
        commands = "\n".join(" ".join(step.get("argv", []))
                             for step in case["steps"])
        self.assertEqual(case["requirements"]["commands"], ["script"])
        self.assertIn("script -qefc", commands)
        self.assertNotIn("| /usr/local/fbase15.15/bin/pg_ctl", commands)


class AssertionTest(unittest.TestCase):
    def test_sql_fails_accepts_product_specific_sqlstate(self):
        process = subprocess.CompletedProcess(
            [], 1, stdout=("ERROR:  XX000: sso and sao permission denied\n"
                           "LOCATION: CheckMacPermission"))
        passed, actual, reason = evaluate(
            sql_fails("sso and sao permission denied"), process)
        self.assertTrue(passed)
        self.assertIn("SQLSTATE=XX000", actual)
        self.assertEqual(reason, "")

    def test_query_assertion_reads_aligned_psql_row(self):
        step = SSO_SYSTEM_PRIVILEGES["steps"][0]
        process = subprocess.CompletedProcess([], 0, stdout=(
            " rolname | rolsuper | rolcreatedb | rolcreaterole | rolreplication | rolbypassrls | rolcanlogin\n"
            "---------+----------+-------------+---------------+----------------+--------------+------------\n"
            " sso     | f        | f           | f             | f              | f            | t\n"
            "(1 row)\n"))
        passed, actual, reason = evaluate_step(step, process)
        self.assertTrue(passed)
        self.assertIn("sso|f|f|f|f|f|t", actual)
        self.assertEqual(reason, "")

    def test_sql_error_assertion_checks_sqlstate_and_message(self):
        step = SSO_SYSTEM_PRIVILEGES["steps"][1]
        process = subprocess.CompletedProcess(
            [], 1, stdout="ERROR:  42501: permission denied\nLOCATION: AlterRole")
        passed, actual, reason = evaluate_step(step, process)
        self.assertTrue(passed)
        self.assertIn("SQLSTATE=42501", actual)
        self.assertEqual(reason, "")

    def test_query_difference_explains_expected_and_actual(self):
        step = SSO_SYSTEM_PRIVILEGES["steps"][0]
        process = subprocess.CompletedProcess([], 0, stdout=(
            " rolname | rolsuper | rolcreatedb | rolcreaterole | rolreplication | rolbypassrls | rolcanlogin\n"
            "---------+----------+-------------+---------------+----------------+--------------+------------\n"
            " sso     | t        | f           | f             | f              | f            | t\n"
            "(1 row)\n"))
        passed, actual, reason = evaluate_step(step, process)
        self.assertFalse(passed)
        self.assertIn("sso|f|f|f|f|f|t", reason)
        self.assertIn("sso|t|f|f|f|f|t", reason)


class ReportTest(unittest.TestCase):
    def test_report_prefers_case_topology_over_managed_environment(self):
        case = dict(SSO_SYSTEM_PRIVILEGES)
        case["test_topology"] = {
            "summary": "节点数=2；无物理备库",
            "nodes": [{"name": "node134", "role": "MMR primary",
                       "host": "127.0.0.1", "port": 15534,
                       "data_dir": "/tmp/node134"}],
            "relations": ["MMR 多活: node134 <-> node135"],
        }
        now = datetime.datetime(2026, 7, 17, 10, 0, 0)
        report = render_case_report(
            case, CaseResult("SUCCESS", [], Path("/tmp/output")), "mmr",
            "env_test", {"fdd_mmr": {}}, [], now, now,
            environment_topology={
                "summary": "节点数=3；物理主备=1",
                "nodes": [{"name": "primary", "role": "primary",
                           "host": "127.0.0.1", "port": 15432,
                           "data_dir": "/tmp/primary"}],
                "relations": ["物理流复制: primary -> standby"],
            })
        self.assertIn("测试运行拓扑:", report)
        self.assertNotIn("节点数=3；物理主备=1", report)
        self.assertNotIn("role=primary host=127.0.0.1 port=15432", report)
        self.assertIn("节点数=2；无物理备库", report)
        self.assertIn("MMR 多活: node134 <-> node135", report)

    def test_failed_report_contains_sql_expected_actual_and_reason(self):
        case = SSO_SYSTEM_PRIVILEGES
        result = CaseResult(
            "FAILED",
            [StepResult(
                case["steps"][0], "FAILED",
                "返回值=sso|t|f|f|f|f|t，退出码=0",
                (
                    " rolname | rolsuper\n"
                    "---------+---------\n"
                    " sso     | t\n"
                    "(1 row)"),
                "预期 SSO 不是超级用户；实际 rolsuper=t",
                node="mac_primary")],
            Path("/tmp/output"),
            server_evidence=["postgresql.log", "postgresql.csv"],
            core_files=["/tmp/mac/core.1234"],
            setting_details=[{
                "node": "mac_primary", "name": "fdb.separate_user",
                "config_sql": (
                    "SELECT sourcefile, sourceline FROM pg_file_settings "
                    "WHERE name = 'fdb.separate_user'"),
                "config_output": (
                    " config_file                 | line | configuration\n"
                    "-----------------------------+------+-------------------------\n"
                    " /tmp/mac/postgresql.conf    | 12   | fdb.separate_user = 'on'\n"
                    "(1 row)"),
                "config_error": "",
                "sql": "SHOW fdb.separate_user", "output": (
                    " fdb.separate_user\n-------------------\n on\n(1 row)"),
                "actual": "on", "requirement": "等于 on",
                "purpose": "启用三权分立机制", "matched": True, "error": "",
            }],
        )
        now = datetime.datetime(2026, 7, 14, 14, 0, 0)
        report = render_case_report(
            case, result, "mac", "env_test", {"fbase_mac": {}},
            [{"name": "mac_primary", "host": "127.0.0.1", "port": 15432,
              "data_dir": "/tmp/mac"}], now, now,
        )
        self.assertIn("postgres=# SELECT rolname", report)
        self.assertIn("rolname | rolsuper", report)
        self.assertIn("分组: mac / separation_of_duties", report)
        self.assertIn("预期结果:", report)
        self.assertIn("失败原因: 预期 SSO 不是超级用户；实际 rolsuper=t", report)
        self.assertIn("证据: execution.log", report)
        self.assertIn("- postgresql.log", report)
        self.assertIn("- postgresql.csv", report)
        self.assertIn("- /tmp/mac/core.1234", report)
        self.assertIn("1. 启用三权分立机制", report)
        self.assertIn("PostgreSQL 生效配置:", report)
        self.assertNotIn("FROM pg_file_settings", report)
        self.assertIn("postgres=# SHOW fdb.separate_user;", report)
        self.assertIn("预期结果: 参数值等于 on", report)

    def test_report_uses_command_report_node(self):
        case = dict(SSO_SYSTEM_PRIVILEGES)
        step = dict(case["steps"][0])
        step["report_node"] = "node134"
        result = CaseResult("SUCCESS", [StepResult(
            step, "SUCCESS", "", "(1 row)", node="forward1_primary")],
            Path("/tmp/output"))
        now = datetime.datetime(2026, 7, 17, 10, 0, 0)
        report = render_case_report(
            case, result, "mmr", "env_test", {}, [], now, now)
        self.assertIn("执行节点: node134", report)
        self.assertNotIn("执行节点: forward1_primary", report)

    def test_report_infers_command_node_from_temporary_topology_port(self):
        case = dict(SSO_SYSTEM_PRIVILEGES)
        step = {"type": "command", "title": "查询临时节点", "node": "mmr:forward1",
                "argv": ["sh", "-ec", "psql -h 127.0.0.1 -p 15534 -c 'SELECT 1' host=127.0.0.1 port=15535"],
                "expected": "返回 1", "assertion": command_succeeds()}
        result = CaseResult("SUCCESS", [StepResult(
            step, "SUCCESS", "", "(1 row)", node="forward1_primary")],
            Path("/tmp/output"))
        now = datetime.datetime(2026, 7, 17, 10, 0, 0)
        report = render_case_report(
            case, result, "mmr", "env_test", {}, [], now, now,
            environment_topology={"nodes": [{"name": "node134", "port": "15534"},
                                               {"name": "node135", "port": "15535"}]})
        self.assertIn("执行节点: node134", report)
        self.assertNotIn("执行节点: node134, node135", report)

    def test_report_maps_internal_selector_to_document_node_name(self):
        case = dict(SSO_SYSTEM_PRIVILEGES)
        step = dict(case["steps"][0])
        step["node"] = "mmr:forward1"
        result = CaseResult("SUCCESS", [StepResult(
            step, "SUCCESS", "", "(1 row)", node="forward1_primary")],
            Path("/tmp/output"))
        now = datetime.datetime(2026, 7, 17, 10, 0, 0)
        report = render_case_report(
            case, result, "mmr", "env_test", {}, [], now, now,
            environment_topology={"report_nodes": {"mmr:forward1": "node134"}})
        self.assertIn("执行节点: node134", report)
        self.assertNotIn("执行节点: forward1_primary", report)
        self.assertNotIn("执行节点: forward1_primary", report)

    def test_report_uses_monotonic_case_duration_when_available(self):
        result = CaseResult("SUCCESS", [], Path("/tmp/output"),
                            duration_seconds=1.25)
        now = datetime.datetime(2026, 7, 14, 14, 0, 0)
        report = render_case_report(
            SSO_SYSTEM_PRIVILEGES, result, "mac", "env_test", {"fbase_mac": {}},
            [{"name": "mac_primary", "host": "127.0.0.1", "port": 15432,
              "data_dir": "/tmp/mac"}], now, now - datetime.timedelta(seconds=1),
        )
        self.assertIn("耗时: 1.250s", report)

    def test_report_renders_absolute_path_shell_utility_with_shell_prompt(self):
        case = dict(SSO_SYSTEM_PRIVILEGES)
        step = {
            "type": "command", "title": "转换物理备库", "node": "mmr:forward1",
            "argv": ["sh", "-ec", "/usr/local/fbase/bin/fdd_mmr_join -D /tmp/node137"],
            "display_sql": "/usr/local/fbase/bin/fdd_mmr_join -D /tmp/node137",
            "expected": "转换成功", "assertion": command_succeeds(),
        }
        result = CaseResult("SUCCESS", [StepResult(
            step, "SUCCESS", "", "join complete", node="forward1_primary")],
            Path("/tmp/output"))
        now = datetime.datetime(2026, 7, 17, 12, 0, 0)
        report = render_case_report(
            case, result, "mmr", "env_test", {}, [], now, now)
        self.assertIn("$ /usr/local/fbase/bin/fdd_mmr_join -D /tmp/node137", report)
        self.assertNotIn("postgres=# /usr/local/fbase/bin/fdd_mmr_join", report)

    def test_conflict_report_states_the_execution_mode(self):
        case = dict(SSO_SYSTEM_PRIVILEGES)
        case["group"] = "conflict"
        result = CaseResult("SUCCESS", [], Path("/tmp/output"))
        now = datetime.datetime(2026, 7, 17, 12, 0, 0)
        report = render_case_report(
            case, result, "mmr", "env_test", {}, [], now, now)
        self.assertIn("冲突处理关键配置:", report)
        self.assertIn("debug_logical_replication_streaming=buffered", report)
        self.assertIn("logical_decoding_work_mem=64MB", report)
        self.assertIn("streaming=off，two_phase=false", report)

    def test_case_can_hide_automatic_setting_details(self):
        case = dict(SSO_SYSTEM_PRIVILEGES)
        case["report_setting_details"] = False
        result = CaseResult("SUCCESS", [], Path("/tmp/output"), setting_details=[{
            "node": "primary", "purpose": "temporary", "sql": "SHOW work_mem",
            "output": " work_mem\\n----------\\n 4MB\\n(1 row)", "actual": "4MB",
            "requirement": "等于 4MB", "matched": True,
        }])
        now = datetime.datetime(2026, 7, 17, 12, 0, 0)
        report = render_case_report(
            case, result, "mmr", "env_test", {}, [], now, now)
        self.assertNotIn("PostgreSQL 生效配置:", report)
        self.assertNotIn("SHOW work_mem", report)

    def test_report_hides_steps_not_executed_after_a_failure(self):
        case = dict(SSO_SYSTEM_PRIVILEGES)
        hidden = {"title": "隐藏初始化", "report": False, "type": "shell",
                  "expected": "初始化成功", "assertion": {"type": "command_succeeds"}}
        executed = dict(case["steps"][0])
        skipped = {"title": "后续操作", "report": True, "type": "sql",
                   "user": "postgres", "database": "postgres", "sql": "SELECT 2",
                   "expected": "返回 2", "assertion": {"type": "command_succeeds"}}
        result = CaseResult("FAILED", [
            StepResult(hidden, "SUCCESS", "初始化成功", "", ""),
            StepResult(executed, "FAILED", "实际失败", "ERROR", "产品错误"),
            StepResult(skipped, "BLOCKED", "未执行", "", "前序步骤失败，当前步骤不再具备有效前置条件"),
        ], Path("/tmp/output"))
        now = datetime.datetime(2026, 7, 17, 12, 0, 0)
        report = render_case_report(
            case, result, "mmr", "env_test", {}, [], now, now)
        self.assertIn("检查 SSO 的初始系统权限", report)
        self.assertNotIn("后续操作", report)
        self.assertNotIn("前序步骤失败，当前步骤不再具备有效前置条件", report)
        self.assertIn("失败/阻塞说明:\n  - 第 1 步: 产品错误", report)

    def test_report_shows_hidden_step_when_it_failed(self):
        case = dict(SSO_SYSTEM_PRIVILEGES)
        hidden = {"title": "隐藏初始化", "report": False, "type": "shell",
                  "expected": "初始化成功", "assertion": {"type": "command_succeeds"}}
        result = CaseResult("FAILED", [
            StepResult(hidden, "FAILED", "启动失败", "Address already in use", "端口被占用"),
        ], Path("/tmp/output"))

        report = render_case_report(
            case, result, "mac", "env", set(), [],
            datetime.datetime.now(), datetime.datetime.now())

        self.assertIn("1. 隐藏初始化", report)
        self.assertIn("端口被占用", report)


class RunnerOutputTest(unittest.TestCase):
    def test_failure_prints_reason_report_and_core_path(self):
        runner = SuiteRunner.__new__(SuiteRunner)
        runner.config = SimpleNamespace(root=Path("/tmp/regress"))
        result = CaseResult(
            "FAILED",
            [StepResult({"title": "执行检查"}, "FAILED", "实际=0", "",
                        "预期=1，实际=0")],
            Path("/tmp/regress/output/mac/group/case"),
            core_files=["/tmp/pgdata/core.1234"],
        )
        output = io.StringIO()
        with redirect_stdout(output):
            runner._print_failure(result)
        rendered = output.getvalue()
        self.assertIn("失败原因: 第 1 步 执行检查: 预期=1，实际=0", rendered)
        self.assertIn("REPORT: output/mac/group/case/report.txt", rendered)
        self.assertIn("CORE: /tmp/pgdata/core.1234", rendered)
        self.assertIn("GDB: gdb postgres /tmp/pgdata/core.1234", rendered)

    def test_run_starts_a_stopped_managed_environment_before_cases(self):
        class Store(object):
            def load(self):
                return {"state": "stopped"}

        class Manager(object):
            def __init__(self):
                self.store = Store()
                self.started = 0

            def status_rows(self):
                return ({}, [("primary", "primary", "127.0.0.1", 15432,
                              "stopped", "-", "unhealthy", "plugins", "/tmp/p")])

            def start(self):
                self.started += 1

        runner = SuiteRunner.__new__(SuiteRunner)
        runner.manager = Manager()
        state = runner._ensure_environment_started()
        self.assertEqual(runner.manager.started, 1)
        self.assertEqual(state["state"], "stopped")

    def test_run_summary_uses_display_status_and_counts(self):
        output = io.StringIO()
        with redirect_stdout(output):
            SuiteRunner._print_run_summary([
                {"status": "SUCCESS"},
                {"status": "SUCCESS"},
                {"status": "FAILED"},
                {"status": "BLOCKED"},
            ], total_duration=12.345,
                known_issues=[("D-012", ["mmr.streaming.example"], True),
                              ("D-017", ["mmr.node_management.example"], False)])
        rendered = output.getvalue()
        self.assertIn("Total:\n", rendered)
        self.assertIn("SUCCESS:2", rendered)
        self.assertIn("FAIL:1", rendered)
        self.assertIn("BLOCKED:1", rendered)
        self.assertIn("总耗时:12.345s", rendered)
        self.assertIn("已登记产品问题:2", rendered)
        self.assertIn("D-012: 本次已执行", rendered)
        self.assertIn("D-017: [LONG-TIME] 默认未执行", rendered)

    def test_known_issues_for_run_marks_only_selected_cases_executed(self):
        runner = SuiteRunner.__new__(SuiteRunner)
        runner.manager = SimpleNamespace(plugins=["fdd_mmr"])
        selected = [case for case in MMR_SUITE["cases"]
                    if case["id"] == "mmr.node_management.online_join_data_retry"]
        issues = runner._known_issues_for_run(selected)
        states = {issue: executed for issue, unused_cases, executed in issues}
        self.assertFalse(states["D-012"])
        self.assertTrue(states["D-017"])

    def test_non_success_details_are_printed_after_case_results(self):
        runner = SuiteRunner.__new__(SuiteRunner)
        runner.config = SimpleNamespace(root=Path("/tmp/regress"))
        result = CaseResult(
            "FAILED", [], Path("/tmp/regress/output/mac/group/case"),
            error="执行失败",
        )
        output = io.StringIO()
        with redirect_stdout(output):
            runner._print_non_success_details([({"id": "mac.group.case"}, result)])
        rendered = output.getvalue()
        self.assertIn("Details:", rendered)
        self.assertIn("mac.group.case", rendered)
        self.assertIn("FAIL", rendered)
        self.assertIn("失败原因: 执行失败", rendered)
        self.assertIn("REPORT: output/mac/group/case/report.txt", rendered)

    def test_known_issue_is_appended_to_failed_case_status(self):
        self.assertEqual(
            SuiteRunner._display_case_status({"known_issue": "D-006"}, "FAILED"),
            "FAIL [D-006]",
        )

    def test_failure_console_reason_is_single_line_and_truncated(self):
        runner = SuiteRunner.__new__(SuiteRunner)
        runner.config = SimpleNamespace(root=Path("/tmp/regress"))
        result = CaseResult(
            "FAILED", [], Path("/tmp/regress/output/mac/group/case"),
            error="第一行\n第二行 " + "x" * 300,
        )
        output = io.StringIO()
        with redirect_stdout(output):
            runner._print_failure(result)
        reason_line = next(line for line in output.getvalue().splitlines()
                           if "失败原因:" in line)
        self.assertTrue(reason_line.endswith("..."))

    def test_unavailable_environment_keeps_declared_settings_in_report_data(self):
        runner = SuiteRunner.__new__(SuiteRunner)
        runner.manager = SimpleNamespace(resolve_node=lambda selector: "mac_primary")
        case = {"requirements": {"settings": [{
            "name": "fdb.separate_user", "equals": "on",
            "purpose": "启用三权分立机制",
        }]}}
        details = runner._complete_setting_details(case, [])
        self.assertEqual(details[0]["actual"], "<未检查>")
        self.assertEqual(details[0]["config_output"], "<未检查>")
        self.assertEqual(details[0]["requirement"], "等于 on")
        self.assertFalse(details[0]["matched"])

    def test_unhealthy_declared_node_blocks_case_before_execution(self):
        runner = SuiteRunner.__new__(SuiteRunner)
        runner.manager = SimpleNamespace(
            resolve_node=lambda selector: {"primary": "primary", "standby": "standby"}[selector],
            status_rows=lambda: ({}, [
                ("primary", "primary", "127.0.0.1", 15432, "running", "false", "healthy", "plugins", "/tmp/p"),
                ("standby", "standby", "127.0.0.1", 15433, "stopped", "-", "unhealthy", "plugins", "/tmp/s"),
            ]),
            nodes={"primary": {}, "standby": {}},
        )
        case = {"steps": [{"node": "standby"}], "fixtures": []}
        self.assertIn("standby=process:stopped,health:unhealthy",
                      runner._case_node_blocker(case))

    def test_isolated_mmr_topology_does_not_depend_on_shared_health(self):
        self.assertTrue(SuiteRunner._uses_isolated_mmr_topology({
            "fixtures": [{"type": "isolated_mmr_node_creation"}],
        }))
        self.assertFalse(SuiteRunner._uses_isolated_mmr_topology({
            "fixtures": ["cluster"],
        }))
