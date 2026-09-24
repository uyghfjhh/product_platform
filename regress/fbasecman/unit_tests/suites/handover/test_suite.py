import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch
import sys

from framework.execution.phased_process import PhaseAction, PhasedProcess
from suites.handover import suite
from suites.handover.manifest import HANDOVER_CASES
from suites.handover.runtime import HandoverRuntime, _handover_port_pair, _relevant_pg_lines
from framework.evidence import StepJournal
from products.fbasecman.config import remove_config_block_line


class HandoverSuiteTest(unittest.TestCase):
    def test_run_case_cleans_product_process_on_keyboard_interrupt(self):
        calls = []
        runtime = SimpleNamespace(stop=lambda: calls.append("stop"))
        case = SimpleNamespace(executor="interrupt", target="handover.interrupt", issue_id=None)

        def interrupt(_runtime):
            raise KeyboardInterrupt()

        with patch.object(suite, "HandoverRuntime", return_value=runtime), \
                patch.dict(suite.EXECUTORS, {"interrupt": interrupt}):
            with self.assertRaises(KeyboardInterrupt):
                suite.run_case(Path("/tmp"), case)
        self.assertGreaterEqual(calls.count("stop"), 1)

    def _render_configuration(self, topology):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        runtime = HandoverRuntime.__new__(HandoverRuntime)
        runtime.case = SimpleNamespace(name="test_%s" % topology,
                                       topology=topology,
                                       target="handover.test",
                                       route_mode="hint")
        runtime.listen_port = 26432
        runtime.read_port = 26433
        runtime.pid_file = root / "fbasecman.pid"
        runtime.workdir = root
        runtime.proxy_log = root / "fbasecman.log"
        runtime.env = SimpleNamespace(config={
            "fbasecman": {"license_dir": "/tmp/license"},
        })
        runtime._prom_port = lambda: 27432
        runtime._configuration_metadata = lambda: {}
        ports = (10011, 10021, 10012, 10022)
        count = 3 if topology in ("rep", "balance") else 4
        datasources = [
            ("pg_%d" % (220 + index * 10), "127.0.0.1", ports[index])
            for index in range(count)
        ]
        runtime._datasources = lambda: list(datasources)
        if topology == "mmr":
            runtime._datasources = lambda: [
                ("pg_220", "127.0.0.1", 10011),
                ("pg_230", "127.0.0.1", 10021),
                ("pg_240", "127.0.0.1", 10012),
                ("pg_250", "127.0.0.1", 10022),
            ]
        return runtime.render_conf().read_text(encoding="utf-8")

    def test_new_configuration_model_is_rendered_for_all_topologies(self):
        for topology in ("mmr", "rep", "balance"):
            rendered = self._render_configuration(topology)
            self.assertIn('storage_db "postgres"', rendered)
            self.assertIn("backend_clusters", rendered)
            self.assertIn("cluster_name", rendered)
            self.assertNotIn("write_datasource_names", rendered)
            self.assertNotIn("read_datasource_names", rendered)
            self.assertNotIn("parted_datasource_names", rendered)
            self.assertNotIn("primary_replica_maps", rendered)
        self.assertIn('write_cluster "mmr_cluster_1"', self._render_configuration("mmr"))
        self.assertIn('promoted_cluster "mmr_cluster_2"', self._render_configuration("mmr"))
        self.assertIn('application_name "pg_240"', self._render_configuration("mmr"))
        self.assertIn('application_name "pg_240"', self._render_configuration("rep"))

    def test_restart_active_configuration_reuses_the_edited_file(self):
        with TemporaryDirectory() as temporary:
            conf = Path(temporary) / "downgraded.conf"
            conf.write_text('group "postgres" {}\n', encoding="utf-8")
            calls = []
            runtime = HandoverRuntime.__new__(HandoverRuntime)
            runtime.process = SimpleNamespace(
                active_conf=conf,
                binary="/opt/fbasecman",
                stop=lambda **kwargs: calls.append(("stop", kwargs)),
                start=lambda path, **kwargs: calls.append(("start", Path(path), kwargs)),
            )
            steps = []
            runtime.record_step = lambda *args, **kwargs: steps.append((args, kwargs))

            self.assertEqual(conf, runtime.restart_active_configuration())

        self.assertEqual("stop", calls[0][0])
        self.assertEqual(("start", conf, {"ready_timeout": 30.0, "record": False}), calls[1])
        self.assertEqual("重启 fbasecman 加载降级配置", steps[0][0][0])

    def test_all_manifest_executors_are_registered(self):
        self.assertEqual(
            set(case.executor for case in HANDOVER_CASES),
            set(suite.EXECUTORS),
        )

    def test_show_marks_long_time_and_source(self):
        text = suite.show()
        self.assertIn("handover.mmr_hint_set_readonly", text)
        self.assertIn("[LONG-TIME]", text)
        self.assertIn("来源=", text)

    def test_result_line_includes_aligned_elapsed_time(self):
        line = suite._result_line("handover.mmr_hint_configuration", "SUCCESS", 1.2345)
        self.assertIn("handover.mmr_hint_configuration", line)
        self.assertIn("SUCCESS", line)
        self.assertTrue(line.endswith("1.234s"))

    def test_pgbench_and_server_counter_parsers(self):
        self.assertEqual(17, suite._pgbench_transactions(
            "number of transactions actually processed: 17\n"))
        self.assertEqual(0, suite._pgbench_transactions(
            "number of transactions actually processed: 17\n"
            "[pgbench timeout after 180s; process group terminated]\n"))
        output = """ read_request_count | write_request_count
--------------------+---------------------
                  4 |                   5
                  6 |                   7
(2 rows)
"""
        self.assertEqual((10, 12), suite._server_request_counts(output))
        summary = suite._pgbench_summary(
            "pgbench: client 0 sending SELECT 1\n"
            "number of transactions actually processed: 17\n"
            "number of failed transactions: 0 (0.000%)\n"
            "tps = 123.4\n")
        self.assertNotIn("client 0", summary)
        self.assertIn("transactions actually processed: 17", summary)
        self.assertEqual("ISO, DMY", suite._marker_value(" enabled_datestyle=ISO, DMY\n", "enabled_datestyle"))

    def test_server_connection_keys_ignore_changing_statistics(self):
        header = "node_name | user | database | state | addr | port | local_addr | local_port | create_time | read_request | ptr\n"
        first = header + "pg_220 | postgres | postgres | idle | 127.0.0.1 | 10011 | 127.0.0.1 | 50001 | now | 0 | server-a\n"
        later = header + "pg_220 | postgres | postgres | idle | 127.0.0.1 | 10011 | 127.0.0.1 | 50001 | later | 99 | server-a\n"
        self.assertEqual(suite._server_connection_keys(first),
                         suite._server_connection_keys(later))

    def test_pg_report_window_is_scoped_to_action(self):
        window = """===== LOG: pg.log =====
LOG:  statement: SELECT 1;
LOG:  statement: UPDATE unrelated SET x = 1;
"""
        self.assertIn("SELECT 1", _relevant_pg_lines(window, "SELECT 1;"))
        self.assertNotIn("UPDATE unrelated", _relevant_pg_lines(window, "SELECT 1;"))
        self.assertIn("控制台命令不转发", _relevant_pg_lines(window, None))

    def test_parse_failure_log_evidence_keeps_cache_deletion(self):
        proxy = """(remote client) Parse
(outstanding_request) added P
(outstanding_request) added B
(outstanding_request) added D
(outstanding_request) added E
(outstanding_request) added S
ErrorResponse
(outstanding_request) deleted backend cache: key=1234
(outstanding_request) fb_clear_outstanding_requests_until: cleared P
"""
        evidence = suite._parse_failure_log_evidence(proxy)
        self.assertNotIn("added P", evidence)
        self.assertIn("deleted backend cache", evidence)
        self.assertIn("cleared P", evidence)

    def test_jdbc_routing_driver_has_document_group_pause_points(self):
        source = Path(suite.__file__).parent / "assets" / "jdbc" / "HandoverJdbcRouting.java"
        text = source.read_text(encoding="utf-8")
        for phase in ("OLD_11", "OLD_12", "OLD_13", "OLD_14",
                      "NEW_11", "NEW_12", "NEW_13", "NEW_14"):
            self.assertIn('pause("%s")' % phase, text)
        self.assertIn('System.out.println("PHASE_PAUSE=" + phase)', text)
        self.assertIn('CONTROL.readLine()', text)

    def test_global_jdbc_drivers_have_phase_pause_points(self):
        assets = {
            "HandoverGlobalPrepared.java": ("PREPARED_ROWS", "READ_ONLY_ONE", "BEGIN_READ_ONLY"),
            "HandoverGlobalCache.java": ("SPECIAL_ROUTING", "EVICT_4", "BYPASS_GUC_SECOND"),
            "HandoverGucReuse.java": ("FIRST_INITIAL", "SECOND_REUSE", "COMMITS"),
        }
        root = Path(suite.__file__).parent / "assets" / "jdbc"
        for name, markers in assets.items():
            text = (root / name).read_text(encoding="utf-8")
            self.assertIn('System.out.println("PHASE_PAUSE=" + phase)', text)
            self.assertIn("control.readLine()", text)
            for marker in markers:
                self.assertIn('pause(control, "%s")' % marker, text)

    def test_global_prepared_default_phase_has_a_business_readable_report_title(self):
        source = Path(suite.__file__).parent / "suite.py"
        text = source.read_text(encoding="utf-8")

        self.assertIn('"DEFAULT": "默认读写状态查询后（未执行读写切换）"', text)

    def test_global_prepared_manifest_explains_document_test_content(self):
        case = next(item for item in HANDOVER_CASES if item.name == "global_prepared_statements")
        self.assertEqual(5, len(case.notes))
        self.assertEqual("PreparedStatements功能是否正常，切换节点后能否正常使用。", case.notes[0])
        self.assertEqual("控制台显示的缓存信息，缓存统计信息是否正确。", case.notes[4])
        self.assertEqual(21, len(case.step_mapping))
        self.assertEqual(("10", "1、2、3", "BEGIN READ ONLY 的结果、读路由和 BEGIN_READ_ONLY"),
                         case.step_mapping[9])

    def test_global_cache_rows_match_exact_description_and_fields(self):
        output = """ global_name | description | sql_class | has_bypass_response | ref_count
-------------+-------------+-----------+---------------------+----------
 __fbasecman_1 | SELECT 1 | HEARTBEAT | 1 | 0
 __fbasecman_2 | SELECT $1 | NORMAL | 0 | 1
(2 rows)
"""
        self.assertEqual(2, len(suite._global_cache_rows(output)))
        self.assertTrue(suite._cache_row_matches(output, "SELECT 1", "HEARTBEAT", "1", "0"))
        self.assertFalse(suite._cache_row_matches(output, "SELECT 1", "HEARTBEAT", "0"))
        self.assertEqual(2, suite._psql_row_count(output))

    def test_jdbc_read_backend_pairs_keep_each_read_interval_on_one_reader(self):
        output = (
            "OLD_11_2_TXN1_READ_BEFORE backend_port=10012 in_recovery=true\n"
            "OLD_11_2_TXN1_READ_AFTER backend_port=10012 in_recovery=true\n"
            "OLD_11_3_TXN1_READ_BEFORE backend_port=10021 in_recovery=false\n"
            "OLD_11_3_TXN1_READ_AFTER backend_port=10021 in_recovery=false\n"
        )
        pairs = suite._jdbc_read_backend_pairs(output)
        self.assertEqual("10012", pairs["OLD_11_2_TXN1"]["before"])
        self.assertEqual("10012", pairs["OLD_11_2_TXN1"]["after"])
        self.assertEqual("10021", pairs["OLD_11_3_TXN1"]["before"])
        self.assertEqual("10021", pairs["OLD_11_3_TXN1"]["after"])

    def test_thread_and_pool_statistics_check_documented_values(self):
        threads = """ thread_id | status | cl_connected | read_ratio | write_ratio | queries_per_sec
-----------+--------+--------------+------------+-------------+----------------
 0 | active | 26 | 60% | 40% | 101
 1 | active | 25 | 60% | 40% | 101
 2 | active | 25 | 60% | 40% | 101
 3 | active | 25 | 60% | 40% | 101
 4 | active | 25 | 60% | 40% | 101
 5 | active | 25 | 60% | 40% | 101
 6 | active | 25 | 60% | 40% | 101
 7 | active | 25 | 60% | 40% | 101
 8 | active | 25 | 60% | 40% | 101
 9 | active | 26 | 60% | 40% | 101
(10 rows)
"""
        records = suite._psql_table_records(threads)
        self.assertEqual(10, len(records))
        self.assertEqual(252, suite._record_sum(records, "cl_connected"))
        self.assertEqual(60.0, suite._average_percent(records, "read_ratio"))
        self.assertEqual(40.0, suite._average_percent(records, "write_ratio"))

        calls = []
        transient = threads.replace("60%", "70%").replace("40%", "30%")
        runtime = SimpleNamespace(
            env=SimpleNamespace(config={"local": {"postgres_dir": "/opt/pg"}}),
            listen_port=26432,
            logs_dir=Path("/tmp"),
            run_command=lambda *args, **kwargs: (
                calls.append(args) or (0, transient if len(calls) == 1 else threads)
            ),
        )
        with patch.object(suite.time, "sleep", return_value=None):
            settled = suite._wait_for_thread_statistics_profile(runtime, 1)
        self.assertEqual(2, len(calls))
        self.assertEqual(threads, settled)

        before = """ node_name | database | user | total_requests | request_per_sec | active_ratio
-----------+----------+------+----------------+-----------------+-------------
 pg_220 | postgres | postgres | 100 | 12.00 | 0%
 pg_240 | postgres | postgres | 90 | 8.00 | 0%
(2 rows)
"""
        after = before.replace("12.00", "0.00").replace("8.00", "0.00")
        self.assertTrue(suite._business_pools_are_stable(
            suite._business_pool_records(before), suite._business_pool_records(after)))
        changed = after.replace(" | 90 |", " | 91 |")
        self.assertFalse(suite._business_pools_are_stable(
            suite._business_pool_records(before), suite._business_pool_records(changed)))

    def test_server_maintenance_uses_ptr_and_offline_state(self):
        output = """ node_name | port | ptr | offline
-----------+------+-----+---------
 pg_220 | 10011 | s1234 | 0
 pg_230 | 10021 | sabcd | 1
(2 rows)
"""
        target = suite._active_server_record(output, "10011")
        self.assertEqual("s1234", target["ptr"])
        self.assertFalse(suite._server_is_offline(output, "s1234"))
        offline = output.replace("s1234 | 0", "s1234 | 1")
        self.assertTrue(suite._server_is_offline(offline, "s1234"))

    def test_global_prepared_final_rows_check_stable_ref_counts(self):
        output = (
            "global_name | description | sql_class | has_bypass_response | ref_count\n"
            "__fbasecman_1 | DELETE FROM handover_global_ps WHERE id IN (1, 2, 3) | NORMAL | 0 | 2\n"
            "(1 row)\n"
        )
        passed, actual = suite._validate_global_ps_rows(
            output, 1, check_final_ref_counts=True,
        )
        self.assertFalse(passed)
        self.assertIn("ref_count=2", actual)

    def test_global_prepared_phase_validator_checks_documented_rows_and_statistics(self):
        rows = []
        for index, (description, sql_class) in enumerate(suite._GLOBAL_PS_ROWS[:2], 1):
            rows.append(" __fbasecman_%s | %s | %s | 0 | 1" % (
                index, description, sql_class,
            ))
        cache = (
            "global_name | description | sql_class | has_bypass_response | ref_count\n" +
            "\n".join(rows) + "\n(2 rows)\n"
        )
        stats = (
            "total_entries | referenced_entries | unreferenced_entries | bypass_entries | capacity | hits | misses | evictions\n"
            "2 | 2 | 0 | 0 | 10000 | 0 | 2 | 0\n(1 row)\n"
        )
        validator = suite._global_ps_phase_validator(first_run=True, write_port="10011")
        result = validator("PREPARED_ROWS", {
            "passed": True,
            "jdbc_output": (
                "SQL=DELETE FROM handover_global_ps WHERE id IN (1, 2, 3)\n"
                "SQL=INSERT INTO handover_global_ps(id, note) VALUES (1, 'name1')\n"
                "SQL=INSERT INTO handover_global_ps(id, note) VALUES (2, 'name2')\n"
                "SQL=INSERT INTO handover_global_ps(id, note) VALUES (3, 'name3')\n"
            ),
            "query_results": {
                "SHOW GLOBAL_PREPARED_STATEMENTS;": cache,
                "SHOW GLOBAL_PREPARED_STATEMENTS_STATS;": stats,
            },
        })
        self.assertTrue(result["passed"])
        self.assertIn("2 条缓存", result["expected"])
        self.assertIn("misses=2", result["expected"])
        self.assertIn("| NORMAL | 0 |", result["actual"])

        invalid = cache.replace(" | NORMAL | 0 |", " | RW_HINT_READ | 0 |", 1)
        result = validator("PREPARED_ROWS", {
            "passed": True,
            "jdbc_output": (
                "SQL=DELETE FROM handover_global_ps WHERE id IN (1, 2, 3)\n"
                "SQL=INSERT INTO handover_global_ps(id, note) VALUES (1, 'name1')\n"
                "SQL=INSERT INTO handover_global_ps(id, note) VALUES (2, 'name2')\n"
                "SQL=INSERT INTO handover_global_ps(id, note) VALUES (3, 'name3')\n"
            ),
            "query_results": {
                "SHOW GLOBAL_PREPARED_STATEMENTS;": invalid,
                "SHOW GLOBAL_PREPARED_STATEMENTS_STATS;": stats,
            },
        })
        self.assertFalse(result["passed"])
        self.assertIn("sql_class=RW_HINT_READ", result["actual"])

    def test_global_prepared_read_phase_requires_row_and_non_writer_route(self):
        rows = []
        for index, (description, sql_class) in enumerate(suite._GLOBAL_PS_ROWS[:5], 1):
            rows.append(" __fbasecman_%s | %s | %s | 0 | 1" % (
                index, description, sql_class,
            ))
        cache = (
            "global_name | description | sql_class | has_bypass_response | ref_count\n" +
            "\n".join(rows) + "\n(5 rows)\n"
        )
        stats = (
            "total_entries | referenced_entries | unreferenced_entries | bypass_entries | capacity | hits | misses | evictions\n"
            "5 | 5 | 0 | 0 | 10000 | 0 | 5 | 0\n(1 row)\n"
        )
        observed = {
            "passed": True,
            "jdbc_output": (
                "READ_ONLY_ONE id=2 note=name2\n"
                "READ_ONLY_ONE backend_port=10021 in_recovery=false\n"
            ),
            "query_results": {
                "SHOW GLOBAL_PREPARED_STATEMENTS;": cache,
                "SHOW GLOBAL_PREPARED_STATEMENTS_STATS;": stats,
            },
        }
        validator = suite._global_ps_phase_validator(first_run=True, write_port="10011")
        self.assertTrue(validator("READ_ONLY_ONE", observed)["passed"])

        observed["jdbc_output"] = observed["jdbc_output"].replace("10021", "10011")
        self.assertFalse(validator("READ_ONLY_ONE", observed)["passed"])

    def test_evidence_step_can_skip_logs_when_console_output_is_the_proof(self):
        with TemporaryDirectory() as temporary:
            runtime = HandoverRuntime.__new__(HandoverRuntime)
            runtime._step_order = 0
            runtime.step_journal = StepJournal(Path(temporary) / "steps.json", "handover.test")
            runtime._write_report = lambda _status: None
            with runtime.evidence_step(
                    "全局缓存控制台检查", console=True,
                    expected="console 行内容符合预期", collect_logs=False) as step:
                step.actual_execution("$ psql -c 'SHOW GLOBAL_PREPARED_STATEMENTS;'", "(7 rows)")
                step.assess("console 行内容符合预期", "(7 rows)", True)
            saved = runtime.step_journal.steps[0]
        self.assertEqual([], saved["evidence"])
        self.assertEqual("PASS", saved["result"])

    def test_jdbc_phase_uses_dynamic_console_expectation_without_log_evidence(self):
        script = (
            "import sys\n"
            "print('PHASE_PAUSE=READY', flush=True)\n"
            "assert sys.stdin.readline().strip() == 'continue'\n"
            "print('DONE', flush=True)\n"
        )
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "Driver.java"
            source.write_text(
                "class Driver { void run(java.sql.Connection c) throws Exception {\n"
                "  c.prepareStatement(\"SELECT 1\").executeQuery();\n"
                "} }\n", encoding="utf-8",
            )
            runtime = HandoverRuntime.__new__(HandoverRuntime)
            runtime._step_order = 0
            runtime.step_journal = StepJournal(root / "steps.json", "handover.test")
            runtime._last_evidence_windows = ("", "")
            runtime._write_report = lambda _status: None
            process = PhasedProcess([sys.executable, "-u", "-c", script], root / "driver.log")
            _, rc, output, _ = runtime.observe_jdbc_phases(
                process, source, "jdbc:postgresql://127.0.0.1:6432/postgres",
                [PhaseAction("ready", "PHASE_PAUSE=READY", "continue")],
                lambda _phase, _marker: {
                    "command": "$ psql -c 'SHOW GLOBAL_PREPARED_STATEMENTS;'",
                    "output": "(7 rows)",
                    "expected": "7 条缓存且 has_bypass_response 均为 0",
                    "actual": "rows=7; bypass=0",
                    "passed": True,
                },
                collect_logs=False,
            )
            saved = runtime.step_journal.steps[0]
        self.assertEqual(0, rc)
        self.assertIn("DONE", output)
        self.assertEqual("7 条缓存且 has_bypass_response 均为 0", saved["expected"])
        self.assertEqual("rows=7; bypass=0", saved["actual"])
        self.assertEqual([], saved["evidence"])

    def test_phase_console_report_keeps_each_command_with_its_full_result(self):
        with TemporaryDirectory() as temporary:
            runtime = SimpleNamespace(
                env=SimpleNamespace(config={"local": {"postgres_dir": "/opt/pgsql"}}),
                listen_port=26432,
                logs_dir=Path(temporary),
            )
            outputs = {
                "SHOW CLIENTS;": "type|user\nC|postgres\n" + "row-%d\n" % 25,
                "SHOW SERVERS;": "type|state\nS|active\n",
            }

            def run_command(command, _logfile, check, record):
                sql = command[-1]
                return 0, outputs[sql]

            runtime.run_command = run_command
            observation = suite._phase_console_observation(
                runtime, "READY", ("SHOW CLIENTS;", "SHOW SERVERS;"), "phase_test"
            )

        self.assertIn("阶段 READY: 依次执行以下 console 查询", observation["command"])
        self.assertIn("-c 'SHOW CLIENTS;'", observation["output"])
        self.assertIn("'SHOW CLIENTS;'", observation["output"])
        self.assertIn("row-25", observation["output"])
        self.assertLess(observation["output"].index("SHOW CLIENTS;"),
                        observation["output"].index("SHOW SERVERS;"))

    def test_remove_group_configuration_keeps_adjacent_blocks(self):
        rendered = '''group "postgres" {
    group_mode "mmr"
    promoted_cluster "mmr_cluster_2"
}
user "postgres" {
    group_names "postgres"
}
'''
        actual = remove_config_block_line(rendered, "group", "postgres", "promoted_cluster")
        self.assertNotIn("promoted_cluster", actual)
        self.assertIn('group_mode "mmr"', actual)
        self.assertIn('user "postgres"', actual)

    def test_port_pair_has_adjacent_ports(self):
        listen_port, read_port = _handover_port_pair(0)
        self.assertGreaterEqual(listen_port, 20000)
        self.assertEqual(listen_port + 1, read_port)

    def test_port_pair_retry_excludes_prior_pair(self):
        first = _handover_port_pair(1)
        retry = _handover_port_pair(1, (first,))
        self.assertNotEqual(first, retry)
        self.assertEqual(retry[0] + 1, retry[1])
