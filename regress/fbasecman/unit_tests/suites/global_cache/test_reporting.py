import re
import unittest
from types import SimpleNamespace

from framework.reporting import (
    ReportCheck, ReportDocument, ReportStep, render_report,
    is_transport_only_success, render_psql_table_from_pipe_text,
)
from suites.global_cache.reporting import _record_or_text, build_structured_report_document
from suites.global_cache.runtime import _validate_report_levels
from suites.global_cache.manifest import formal_case_items
from suites.global_cache.reports.documents import STRUCTURED_REPORT_CASES
from suites.global_cache.reports.runtime import GlobalCacheReportMixin
from suites.global_cache.drivers import jdbc_prepared_operations
from lib.report_utils import render_psql_expanded_from_pipe_text, render_record_from_pipe_row


class ReportingContractTest(unittest.TestCase):
    def test_successful_transport_only_result_is_not_a_business_assertion(self):
        self.assertTrue(is_transport_only_success("psql 命令完成", "PASS"))
        self.assertTrue(is_transport_only_success("JDBC 程序退出成功", "PASS"))
        self.assertTrue(is_transport_only_success("阶段 JDBC driver 退出成功", "PASS"))
        self.assertTrue(is_transport_only_success("JDBC 路由时序程序退出成功", "PASS"))
        self.assertFalse(is_transport_only_success("psql 命令完成", "FAIL"))
        self.assertFalse(is_transport_only_success("路由到 MMR 写节点", "PASS"))

    def test_advanced_registry_accepts_structured_report_cases(self):
        _validate_report_levels()

    def test_every_formal_case_has_a_report_document_route(self):
        generic_cases = [
            case for case in formal_case_items() if case.name not in STRUCTURED_REPORT_CASES
        ]
        for case in generic_cases:
            document = build_structured_report_document(
                case,
                {"report_steps": [], "verification_checks": []},
                "PASS",
                "2026-07-10 10:00:00",
                "2026-07-10 10:00:03",
            )
            self.assertIsInstance(document, ReportDocument, case.name)

    def test_every_formal_case_declares_test_content_and_valid_step_rules(self):
        for case in formal_case_items():
            self.assertTrue(case.test_contents, case.target)
            for expression, content, description in case.step_rules:
                re.compile(expression)
                self.assertTrue(description)
                references = [int(value) for value in re.findall(r"\d+", content)]
                self.assertTrue(references)
                self.assertTrue(all(1 <= value <= len(case.test_contents)
                                    for value in references))

    def test_renderer_keeps_setup_step_and_product_check_separate(self):
        document = ReportDocument(
            target="demo.smoke.reload",
            status="PASS",
            started_at="2026-07-10 10:00:00",
            finished_at="2026-07-10 10:00:03",
            purpose="验证规则 reload 后继续生效。",
            overview_steps=["修改规则并 reload。", "执行业务请求。"],
            steps=[
                ReportStep(
                    "修改规则并 reload",
                    details=[("动作", "console=>RELOAD;")],
                    expected="console 返回 RELOAD。",
                    actual="RELOAD",
                    result="PASS",
                ),
                ReportStep(
                    "执行业务请求",
                    details=[("动作", "client.execute(request)")],
                    checks=[ReportCheck("规则已生效", "返回新路由", "返回 read-node", "PASS")],
                ),
            ],
            pass_reason="关键步骤和产品检测项均通过。",
        )

        report = render_report(document)

        self.assertIn("测试开始时间: 2026-07-10 10:00:00", report)
        self.assertIn("步骤 1: 修改规则并 reload", report)
        self.assertIn("步骤 2: 执行业务请求", report)
        self.assertIn("检测项 1: 规则已生效", report)
        self.assertNotIn("最终判定:", report)

    def test_global_cache_report_maps_steps_to_declared_test_content(self):
        case = SimpleNamespace(
            target="global_cache.mapping", summary="mapping", topology="mmr",
            rw_split_method="hint",
            test_contents=("首次创建缓存。", "第二客户端复用缓存。"),
            step_rules=((r"第二客户端", "2", "检查跨客户端复用"),),
        )
        runtime = GlobalCacheReportMixin()
        runtime.case = case
        document = ReportDocument(
            target=case.target, status="PASS", started_at="x", finished_at="y",
            purpose=case.summary,
            steps=[ReportStep("启动 fbasecman"), ReportStep("第二客户端执行")],
        )

        runtime._apply_case_coverage(document)
        report = render_report(document)

        self.assertIn("测试内容:", report)
        self.assertIn("对应测试内容: 前置条件", report)
        self.assertIn("对应测试内容: 2", report)
        self.assertIn("本步骤检查: 检查跨客户端复用", report)

    def test_heartbeat_structured_report_merges_phased_evidence(self):
        document = ReportDocument(
            target="global_cache.heartbeat", status="PASS", started_at="x", finished_at="y",
            purpose="heartbeat",
            steps=[ReportStep("首次建立缓存"), ReportStep("reload"), ReportStep("再次执行")],
        )
        evidence = [
            ReportStep("初始 prepared SQL 执行后观察 NORMAL entry",
                       intermediate=[{"label": "中间状态", "text": "SHOW GLOBAL"}],
                       evidence=[{"label": "fbasecman 证据", "text": "NORMAL"}]),
            ReportStep("console 执行 reload",
                       execution=[{"label": "实际执行", "text": "psql RELOAD"}]),
            ReportStep("reload 后 prepared SQL 执行完成后观察缓存",
                       intermediate=[{"label": "中间状态", "text": "HEARTBEAT"}]),
            ReportStep("JDBC driver 完成后的业务输出"),
        ]

        GlobalCacheReportMixin._merge_heartbeat_evidence(document, evidence)

        self.assertEqual("SHOW GLOBAL", document.steps[0].intermediate[0]["text"])
        self.assertEqual("psql RELOAD", document.steps[1].execution[0]["text"])
        self.assertEqual("HEARTBEAT", document.steps[2].intermediate[0]["text"])

    def test_renderer_keeps_multiline_evidence_readable(self):
        document = ReportDocument(
            target="demo.smoke.multiline",
            status="PASS",
            started_at="2026-07-10 10:00:00",
            finished_at="2026-07-10 10:00:03",
            purpose="验证多行证据缩进。",
            steps=[
                ReportStep(
                    "执行 JDBC 请求",
                    details=[("driver 核心调用", "conn.prepareStatement(sql);\nps.executeQuery();")],
                )
            ],
        )

        report = render_report(document)

        self.assertIn("    driver 核心调用:", report)
        self.assertIn("      conn.prepareStatement(sql);", report)
        self.assertIn("      ps.executeQuery();", report)

    def test_renderer_renders_step_execution_state_evidence_and_key_expectation(self):
        document = ReportDocument(
            target="demo.step_evidence", status="PASS", started_at="x", finished_at="y",
            purpose="验证步骤级取证。",
            steps=[ReportStep(
                "执行查询",
                execution=[{"label": "实际执行", "text": "$ psql -c 'SELECT 1'\n\n ?column? \n----------\n        1"}],
                intermediate=[{"label": "中间状态", "text": "$ psql -d console -c 'SHOW SERVERS;'\n\npg_220 | active"}],
                evidence=[{"label": "fbasecman 证据", "text": "routing to pg_220"},
                          {"label": "PG 证据", "text": "statement: SELECT 1"}],
                key_expected="查询返回 1，且路由到可用后端。",
                actual="返回 1",
                result="PASS",
            )],
        )

        report = render_report(document)

        self.assertLess(report.index("实际执行:"), report.index("中间状态:"))
        self.assertLess(report.index("中间状态:"), report.index("fbasecman 证据:"))
        self.assertLess(report.index("fbasecman 证据:"), report.index("PG 证据:"))
        self.assertIn("关键期望: 查询返回 1，且路由到可用后端。", report)

    def test_pipe_psql_output_is_rendered_as_a_table(self):
        rendered = render_psql_table_from_pipe_text("name|state\npg_220|active\n")

        self.assertEqual(
            "name   | state \n-------+-------\npg_220 | active\n(1 row)", rendered
        )

    def test_console_record_renderer_does_not_truncate_rows_or_values(self):
        long_value = "x" * 300
        output = "name|value\n" + "\n".join(
            "entry_%d|%s" % (index, long_value if index == 21 else index)
            for index in range(1, 23)
        ) + "\n"

        rendered = render_psql_expanded_from_pipe_text(output)

        self.assertIn("-[ RECORD 22 ]--------", rendered)
        self.assertIn(long_value, rendered)
        self.assertNotIn("records omitted", rendered)

    def test_console_record_view_does_not_truncate_a_value(self):
        long_value = "x" * 300

        rendered = render_record_from_pipe_row(
            "entry_01|%s" % long_value, ["global_name", "description"]
        )

        self.assertIn(long_value, rendered)
        self.assertNotIn("[len=", rendered)

    def test_global_cache_record_evidence_renders_as_a_record(self):
        rendered = _record_or_text(
            "__fbasecman_1|SELECT 124|HEARTBEAT|1|0"
        )

        self.assertIn("global_name", rendered)
        self.assertIn("HEARTBEAT", rendered)
        self.assertNotIn("(0 rows)", rendered)

    def test_multiline_console_evidence_uses_heartbeat_record_format(self):
        rendered = _record_or_text(
            "容量=4；最终条目数=2\n"
            "__fbasecman_1|SELECT 1|NORMAL|0|0\n"
            "__fbasecman_2|SELECT 2|NORMAL|0|1"
        )

        self.assertIn("容量=4；最终条目数=2", rendered)
        self.assertIn("-[ RECORD 1 ]--------", rendered)
        self.assertIn("-[ RECORD 2 ]--------", rendered)
        self.assertIn("description         | SELECT 1", rendered)
        self.assertNotIn("__fbasecman_1|SELECT 1", rendered)

    def test_check_actual_appends_console_stats_record(self):
        from suites.global_cache.reports.helpers import check_actual

        rendered = check_actual({
            "actual": "容量=4；最终条目数=4",
            "console_stats": {
                "total_entries": "4", "referenced_entries": "2",
                "unreferenced_entries": "2", "bypass_entries": "0",
                "capacity": "4", "hits": "0", "misses": "6", "evictions": "2",
            },
        })

        self.assertIn("控制台统计:", rendered)
        self.assertIn("-[ RECORD 1 ]--------", rendered)
        self.assertIn("total_entries        | 4", rendered)
        self.assertIn("evictions            | 2", rendered)

    def test_generic_runtime_steps_use_structured_contract(self):
        case = SimpleNamespace(
            name="prepare_before_bind_deploy",
            target="global_cache.prepare_before_bind_deploy",
            summary="验证 Bind 前补部署。",
        )
        summary = {
            "fbasecman_config_lines": ["server_lifetime 3600"],
            "report_steps": [
                {
                    "title": "执行 libpq driver",
                    "command": "driver conninfo mmr_hint",
                    "output": "业务 SQL 执行完成",
                    "note": "driver 核心调用: PQprepare() -> PQexecPrepared()",
                    "sql_operations": [{
                        "sql": "SELECT $1::int /* seed */",
                        "parameters": ["$1=1"],
                    }],
                },
                {
                    "title": "console=>SHOW SERVER_PREP_STMTS;",
                    "output": "目标 server entry 已存在",
                },
            ],
            "verification_checks": [
                {
                    "title": "Bind 前完成补部署",
                    "expected": "server cache 存在目标 SQL",
                    "actual": "目标 server entry 已存在",
                    "result": "PASS",
                }
            ],
        }

        report = render_report(build_structured_report_document(
            case, summary, "PASS", "2026-07-10 10:00:00", "2026-07-10 10:00:03",
            pass_reason="补部署检查通过。",
        ))

        self.assertIn("测试步骤概览:", report)
        self.assertIn("步骤 1: 执行 libpq driver", report)
        self.assertIn("driver 核心调用: PQprepare() -> PQexecPrepared()", report)
        self.assertIn("SQL: SELECT $1::int /* seed */", report)
        self.assertIn("参数: $1=1", report)
        self.assertNotIn("fbasecman 实际协议日志:", report)
        self.assertIn("步骤 2: console=>SHOW SERVER_PREP_STMTS;", report)
        self.assertIn("检测项 1: Bind 前完成补部署", report)
        self.assertNotIn("过程与原始观察:", report)
        self.assertNotIn("断言结果:", report)

    def test_runtime_adapter_keeps_actions_visible_without_fabricated_judgment(self):
        runtime = GlobalCacheReportMixin()
        runtime.case = SimpleNamespace(name="reuse_single_and_cross_client")
        runtime.summary = {"status": "PASS"}
        runtime.step_records = [
            {"title": "编译 JDBC driver", "output": "warning", "rc": 0},
            {"title": "单连接复用: 编译 JDBC driver", "output": "", "rc": 0},
            {
                "title": "执行 JDBC driver",
                "output": "single_connection_ok=true",
                "rc": 0,
                "command": "java GC_basic_reuse",
            },
        ]

        steps = runtime._structured_report_steps()

        self.assertEqual(1, len(steps))
        self.assertNotIn("expected", steps[0])
        self.assertNotIn("actual", steps[0])
        self.assertNotIn("result", steps[0])

    def test_staged_console_output_replaces_standalone_snapshots(self):
        runtime = GlobalCacheReportMixin()
        runtime.step_journal = SimpleNamespace(steps=[
            {
                "title": "console 查询: SHOW GLOBAL_PREPARED_STATEMENTS;",
                "order": 1, "execution": [], "intermediate": [], "evidence": [],
                "expected": "psql 命令完成", "result": "PASS",
            },
            {
                "title": "首次 PreparedStatement 后观察 global cache",
                "order": 2, "execution": [],
                "intermediate": [{
                    "text": "阶段 after_first: console 查询 global/server cache，并采集 fbasecman/PG 日志窗口\n"
                            "$ psql -c 'SHOW GLOBAL_PREPARED_STATEMENTS;'\n"
                            "entry_01\nentry_22",
                }],
                "evidence": [], "expected": "连接保持", "actual": "已观察", "result": "PASS",
            },
        ])

        steps = runtime._evidence_document_steps()

        self.assertTrue(runtime._has_staged_console_evidence())
        self.assertEqual(["首次 PreparedStatement 后观察 global cache"], [step.title for step in steps])
        self.assertIn("entry_22", steps[0].intermediate[0]["text"])

    def test_jdbc_asset_sql_and_bind_values_are_reportable(self):
        from pathlib import Path

        source = Path(__file__).parents[3] / "suites" / "global_cache" / "assets" / "jdbc" / "GC_basic_reuse.java"
        operations = jdbc_prepared_operations(source)

        self.assertEqual("select name from test where id = ? /* gc_basic_reuse */", operations[0]["sql"])
        self.assertEqual(["$1=i + 1"], operations[0]["parameters"])

    def test_jdbc_parameters_stay_with_their_prepared_statement_scope(self):
        from pathlib import Path

        source = Path(__file__).parents[3] / "suites" / "handover" / "assets" / "jdbc" / "HandoverGlobalPrepared.java"
        operations = jdbc_prepared_operations(source)
        by_sql = {item["sql"]: item["parameters"] for item in operations}
        self.assertEqual([], by_sql["SELECT inet_server_port() AS backend_port, pg_is_in_recovery() AS in_recovery"])
        self.assertEqual(["$1=id"], by_sql["SELECT note FROM handover_global_ps WHERE id = ? /* handover_global_ps */"])
        self.assertEqual(["$1=id", "$2=note"], by_sql["INSERT INTO handover_global_ps(id, note) VALUES (?, ?)"])

    def test_specialised_report_gets_explicit_execution_interruption(self):
        document = ReportDocument(
            target="global_cache.capacity", status="FAIL", started_at="x", finished_at="y",
            purpose="failure", steps=[ReportStep("已完成的前置动作", result="PASS")],
        )
        GlobalCacheReportMixin._append_failure_step(document, {
            "title": "执行外置 JDBC driver", "command": "java Driver SELECT 124",
            "output": "Connection refused", "expected": "command exits with rc=0",
            "actual": "command exited with rc=1",
        })
        report = render_report(document)

        self.assertIn("步骤 2: 执行中断: 执行外置 JDBC driver", report)
        self.assertIn("失败命令: java Driver SELECT 124", report)
        self.assertIn("原始输出: Connection refused", report)
        self.assertIn("判定: FAIL", report)

    def test_composite_runtime_hides_phase_prefixed_startup_and_config_steps(self):
        runtime = GlobalCacheReportMixin()
        runtime.case = SimpleNamespace(name="statement_mapping_lifecycle")
        runtime.summary = {"status": "PASS"}
        runtime.step_records = [
            {"title": "unnamed statement 覆盖: 启动运行配置: server_lifetime 10"},
            {"title": "unnamed statement 覆盖: 运行配置: server_lifetime 10"},
            {"title": "unnamed statement 覆盖: 启动 fbasecman", "rc": 0},
            {"title": "unnamed statement 覆盖: 编译 libpq driver", "rc": 0},
            {"title": "unnamed statement 覆盖: 验证: cache entry 已创建", "rc": 0},
            {
                "title": "unnamed statement 覆盖: 执行 libpq driver",
                "note": "driver 核心调用: PQprepare() -> PQexecPrepared()",
                "rc": 0,
            },
        ]

        steps = runtime._structured_report_steps()

        self.assertEqual(1, len(steps))
        self.assertEqual(
            "unnamed statement 覆盖: 执行 libpq driver", steps[0]["title"]
        )

    def test_generic_document_never_renders_raw_driver_command(self):
        case = SimpleNamespace(
            name="demo", target="global_cache.demo", summary="验证报告命令脱敏。"
        )
        document = build_structured_report_document(
            case,
            {
                "report_steps": [{
                    "title": "执行 JDBC driver",
                    "command": "/home/postgres/workdir/java -cp /tmp/driver.jar Demo",
                    "output": "ok",
                    "result": "PASS",
                }],
                "verification_checks": [],
            },
            "PASS", "2026-07-10 10:00:00", "2026-07-10 10:00:03",
        )

        report = render_report(document)

        self.assertNotIn("/home/postgres", report)
        self.assertNotIn("/tmp/driver.jar", report)

    def test_composite_actual_with_pipe_is_not_misread_as_server_record(self):
        actual = (
            "libpq=conn2_second_ok=name | "
            "after_reuse2=__fbasecman_1|select 1|NORMAL|0|2"
        )

        self.assertEqual(actual, _record_or_text(actual))

    def test_composite_pass_reason_summarizes_phase_checks(self):
        runtime = GlobalCacheReportMixin()
        runtime.case = SimpleNamespace(name="reuse_single_and_cross_client")
        runtime.summary = {
            "verification_checks": [
                {"title": "单连接复用: miss/create/hit", "result": "PASS"},
                {"title": "跨客户端复用: 命中已有 entry", "result": "PASS"},
            ]
        }

        reason = runtime._build_pass_reason()

        self.assertIn("共 2 个产品行为检测项通过", reason)
        self.assertIn("单连接复用", reason)
        self.assertIn("跨客户端复用", reason)

    def test_guc_reload_renders_both_phases_and_reload_step(self):
        case = SimpleNamespace(
            name="reload_enable_guc_sync_existing_entry",
            target="global_cache.reload_enable_guc_sync_existing_entry",
            summary="验证 reload 后 GUC 同步语义切换。",
        )
        operation = {
            "output_key": "set_guc",
            "mode": "statement",
            "sql": "SET extra_float_digits = 3",
        }
        summary = {
            "reload_toggle": {
                "start": "no",
                "after": "yes",
                "guc_name": "extra_float_digits",
                "guc_value": "3",
                "expected_before": "1",
                "expected_after": "3",
            },
            "reload_result": "RELOAD",
            "guc_reload_sequences": {
                "before": {"operations": [operation], "output": "before_reload_current_setting=1\n"},
                "after": {"operations": [operation], "output": "after_reload_current_setting=3\n"},
            },
            "verification_checks": [],
        }

        document = build_structured_report_document(
            case, summary, "PASS", "2026-07-10 10:00:00", "2026-07-10 10:00:03"
        )
        report = render_report(document)

        self.assertIn("步骤 1: reload 前执行 GUC 同步验证", report)
        self.assertIn("步骤 2: 修改 enable_guc_sync 并 reload", report)
        self.assertIn("步骤 3: reload 后再次执行 GUC 同步验证", report)
        self.assertIn("初始: enable_guc_sync no", report)
        self.assertIn("reload 后: enable_guc_sync yes", report)

    def test_capacity_reload_uses_structured_product_config_report(self):
        case = SimpleNamespace(
            name="global_capacity_reload_shrink",
            target="global_cache.global_capacity_reload_shrink",
            summary="验证 reload 缩容。",
        )
        summary = {
            "capacity_before_limit": 10,
            "capacity_after_limit": 5,
            "capacity_server_lifetime": 10,
            "reload_result": "RELOAD",
            "jdbc_sequence": [
                {"output_key": "seed_01", "mode": "query_int:1", "sql": "SELECT ?"}
            ],
            "jdbc_sequence_output": "seed_01=1\n",
            "verification_checks": [
                {"title": "shrink 前 seeded entries 已全部进入 unref 状态", "expected": "ref_count=0", "actual": "1 条", "result": "PASS"},
                {"title": "reload 后 capacity 收缩到新上限", "expected": "capacity=5", "actual": "capacity=5", "result": "PASS"},
            ],
        }

        report = render_report(build_structured_report_document(
            case, summary, "PASS", "2026-07-10 10:00:00", "2026-07-10 10:00:03"
        ))

        self.assertIn("关键配置:", report)
        self.assertIn("初始: global_prepared_statements_limit 10", report)
        self.assertIn("步骤 3: 修改容量配置并 reload", report)
        self.assertIn("检测项 1: reload 后 capacity 收缩到新上限", report)
        self.assertNotIn("prepareThreshold", report)
        self.assertNotIn("运行配置:", report)
        self.assertNotIn("过程与原始观察:", report)
        self.assertNotIn("断言结果:", report)

    def test_active_ref_capacity_uses_phased_structured_report(self):
        case = SimpleNamespace(
            name="ref_count_protects_active_entries",
            target="global_cache.ref_count_protects_active_entries",
            summary="验证 active 引用保护。",
        )
        summary = {
            "phased_prepared_operations": [
                {"output_key": "active_01", "sql": "SELECT ?", "bind_value": 1},
            ],
            "phased_prepared_output": "active_01=1\nPHASE=READY\nPHASE=DONE\n",
            "verification_checks": [
                {"title": "active refs 优先于容量约束", "expected": "保持引用", "actual": "referenced_entries=1", "result": "PASS"},
            ],
            "core_result": {
                "capacity": 2,
                "matched_count": 1,
                "matched_entries": ["__fbasecman_1|SELECT $1|NORMAL|0|1"],
            },
        }

        report = render_report(build_structured_report_document(
            case, summary, "PASS", "2026-07-10 10:00:00", "2026-07-10 10:00:03"
        ))

        self.assertIn("关键配置:", report)
        self.assertIn("global_prepared_statements_limit 2", report)
        self.assertIn("1 个连接在观察期间保持活动", report)
        self.assertNotIn("PHASE=READY", report)
        self.assertIn("检测项 2: 活动业务 entries 均保留在 global cache", report)
        self.assertNotIn("运行配置:", report)
        self.assertNotIn("Runtime Conf:", report)
        self.assertNotIn("过程与原始观察:", report)
        self.assertNotIn("过程与原始观察:", report)
