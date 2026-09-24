import json
import os
import subprocess
import unittest
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from suites.stable.config import LEGACY_FIELDS, StableConfig, parse_duration, render_fbasecman_config
from suites.stable.manifest import WORKLOADS, enabled_workloads, find_workload
from suites.stable.runtime import (
    StableRuntime, choose_ports, jdbc_result, managed_pid, pg_log_archive_script, pg_log_window_script,
    pgbench_result, refresh_status, route_evidence, route_startup_pending, run_progress,
    scan_logs, workload_progress, workload_status_text, write_report_from_state,
    runtime_for_state, workload_log_summary,
)
from suites.stable.state import StateStore
from suites.stable.supervisor import _observe_workloads, supervise
from tools.stable_cli import parser
from tools.stable_top import dashboard, run_top, run_tui
from env.postgres import _mmr_nodes, _rep_nodes


ROOT = Path(__file__).resolve().parents[3]


class StableTest(unittest.TestCase):
    def test_manifest_contains_business_and_ha_workloads(self):
        self.assertEqual(13, len(WORKLOADS))
        self.assertEqual(12, len([item for item in WORKLOADS if item.kind == "pgbench"]))
        self.assertEqual("jdbc.prepared_leak", find_workload("stable.jdbc.prepared_leak").name)

    def test_ha_workloads_follow_stable_yaml_switch(self):
        enabled = enabled_workloads({"ha_commands": {"enabled": True}})
        disabled = enabled_workloads({"ha_commands": {"enabled": False}})

        self.assertEqual(13, len(enabled))
        self.assertEqual(9, len(disabled))

    def test_jdbc_workload_follows_stable_yaml_switch(self):
        enabled = enabled_workloads({"jdbc": {"enabled": True}})
        disabled = enabled_workloads({"jdbc": {"enabled": False}})

        self.assertEqual(13, len(enabled))
        self.assertEqual(12, len(disabled))

    def test_reload_status_toggle_follows_stable_yaml_switch(self):
        enabled = enabled_workloads({"reload_status_toggle": {"enabled": True}})
        disabled = enabled_workloads({"reload_status_toggle": {"enabled": False}})

        self.assertEqual(13, len(enabled))
        self.assertEqual(12, len(disabled))

    def test_enabled_workloads_can_narrow_the_run(self):
        workloads = enabled_workloads({
            "enabled_workloads": ["pgbench.reload_status_toggle"],
            "reload_status_toggle": {"enabled": True},
        })

        self.assertEqual(["pgbench.reload_status_toggle"], [item.name for item in workloads])

    def test_official_durations_are_preserved(self):
        cfg = StableConfig(ROOT)
        self.assertEqual(2400, cfg.duration("pgbench"))
        self.assertEqual(1200, cfg.duration("jdbc"))
        self.assertEqual(5400, parse_duration("1h30m"))

    def test_uses_stable_config_and_its_local_override(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            stable_text = (ROOT / "stable.yaml").read_text(encoding="utf-8")
            stable_text = stable_text.replace("output_dir: output", "output_dir: stable-output")
            stable_text = stable_text.replace("pgbench_duration: 40m", "pgbench_duration: 7m")
            (root / "stable.yaml").write_text(stable_text, encoding="utf-8")
            (root / "stable.local.yaml").write_text(
                "stable:\n  pgbench_duration: 8m\n", encoding="utf-8"
            )
            cfg = StableConfig(root)

        self.assertEqual(480, cfg.duration("pgbench"))
        self.assertEqual(root / "stable-output" / "stable", cfg.output_dir)

    def test_stable_environment_topology_is_separate_from_regression(self):
        cfg = StableConfig(ROOT)
        db = cfg.runtime_config.config["database"]

        self.assertEqual("/home/postgres/fbasecman_stable_v2_mmr", db["mmr_data_root"])
        self.assertEqual("/home/postgres/fbasecman_stable_v2_rep", db["rep_data_root"])
        self.assertEqual(12011, db["ports"]["mmr1"])
        self.assertEqual(12051, db["ports"]["rep_primary"])
        self.assertEqual(ROOT / "output" / "stable" / "env", cfg.runtime_config.env_output_dir)
        self.assertTrue(all("fbasecman_stable_v2" in path for _, path in _mmr_nodes(cfg.runtime_config)))
        self.assertTrue(all("fbasecman_stable_v2" in path for _, path in _rep_nodes(cfg.runtime_config)))

    def test_cli_parses_independent_stable_environment_commands(self):
        args = parser().parse_args(["env", "setup"])
        self.assertEqual("env", args.command)
        self.assertEqual("setup", args.env_command)

    def test_top_text_flag_remains_a_compatible_alias(self):
        args = parser().parse_args(["top", "--text", "--once"])
        self.assertTrue(args.text)
        self.assertTrue(args.once)

    def test_prefers_stable_listener_ports_when_available(self):
        with patch("suites.stable.runtime._port_free", return_value=True):
            self.assertEqual((26432, 27432, 26434), choose_ports(26432, 27432))

    def test_workload_progress_reports_elapsed_remaining_and_planned_end(self):
        cfg = type("Config", (), {"duration": lambda _self, kind: 2400 if kind == "pgbench" else 1200})()
        state = {"started_at": 100, "workloads": {
            "pgbench.balance": {"status": "running", "started_at": 110, "duration_seconds": 2400},
            "jdbc.prepared_leak": {"status": "running", "started_at": 120, "duration_seconds": 1200},
        }}
        workload = workload_progress(cfg, state, "pgbench.balance", state["workloads"]["pgbench.balance"], now=710)
        overall = run_progress(cfg, state, now=710)

        self.assertEqual("10m00s", workload["elapsed_text"])
        self.assertEqual("30m00s", workload["remaining_text"])
        self.assertEqual(2510, workload["ends_at"])
        self.assertEqual("10m10s", overall["elapsed_text"])
        self.assertEqual("30m00s", overall["remaining_text"])

    def test_workload_terminal_status_includes_real_failure_result(self):
        self.assertEqual("PASS", workload_status_text({"status": "completed"}))
        self.assertEqual("NOT_STARTED", workload_status_text({"status": "pending"}))
        self.assertEqual("FAIL(client_failures=4, rc=1)", workload_status_text({
            "status": "failed", "result": {"failures": 4, "returncode": 1},
        }))

    def test_refactored_configuration_has_no_legacy_fields(self):
        cfg = StableConfig(ROOT)
        with TemporaryDirectory() as directory:
            rendered = render_fbasecman_config(cfg, Path(directory), 26432, 27432)
        self.assertIn('backend_clusters "mmr_cluster_1,mmr_cluster_2"', rendered)
        self.assertIn('write_cluster "mmr_cluster_1"', rendered)
        self.assertIn('application_name "pg_240"', rendered)
        self.assertEqual(11, rendered.count('status "active"'))
        self.assertIn('log_query no', rendered)
        self.assertIn('log_session no', rendered)
        self.assertIn('log_debug no', rendered)
        self.assertIn('coroutine_stack_size 16', rendered)
        self.assertEqual(12, rendered.count('storage_user "postgres"'))
        self.assertNotIn('storage_user "mmrhint"', rendered)
        for field in LEGACY_FIELDS:
            self.assertNotIn(field, rendered)

    def test_documented_stable_config_example_loads_and_renders(self):
        example = ROOT / "suites" / "stable" / "assets" / "config" / "stable.example.yaml"
        cfg = StableConfig(ROOT, [example])
        with TemporaryDirectory() as directory:
            rendered = render_fbasecman_config(cfg, Path(directory), 26432, 27432)
        self.assertIn('cluster_name "mmr_cluster_1"', rendered)
        self.assertIn('status "active"', rendered)

    def test_repository_smoke_override_keeps_stable_topology_and_shortens_workloads(self):
        smoke = ROOT / "suites" / "stable" / "assets" / "config" / "stable.smoke.yaml"
        cfg = StableConfig(ROOT, [smoke])
        self.assertEqual(20, cfg.duration("pgbench"))
        self.assertEqual(20, cfg.duration("jdbc"))
        self.assertEqual(2, cfg.values["jdbc"]["short_conn_clients"])

    def test_pgbench_result_is_strict(self):
        good = "number of transactions actually processed: 10\nnumber of failed transactions: 0 (0.0%)\n"
        bad = "number of transactions actually processed: 10\nnumber of failed transactions: 1 (1.0%)\n"
        self.assertTrue(pgbench_result(good)["ok"])
        self.assertFalse(pgbench_result(bad)["ok"])

    def test_ha_retry_summary_accepts_refresh_scheduling_retry(self):
        result = pgbench_result(
            "HA_RETRY_SUMMARY attempts=9 successes=2 reload_busy=6 "
            "refresh_retry=1 unexpected_failures=0\n")
        self.assertTrue(result["ok"])
        self.assertEqual(1, result["refresh_retry"])

    def test_ha_retry_summary_remains_backward_compatible(self):
        result = pgbench_result(
            "HA_RETRY_SUMMARY attempts=3 successes=1 reload_busy=2 "
            "unexpected_failures=0\n")
        self.assertTrue(result["ok"])
        self.assertEqual(0, result["refresh_retry"])

    def test_jdbc_result_rejects_any_failure_counter(self):
        good = "RESULT: PASS success=9 failures=0 prepares=3 rw_switches=1 rw_switch_failures=0 heartbeats=1 heartbeat_failures=0 guc_ops=1 guc_failures=0"
        bad = good.replace("guc_failures=0", "guc_failures=1")
        self.assertTrue(jdbc_result(good)["ok"])
        self.assertFalse(jdbc_result(bad)["ok"])

    def test_jdbc_fingerprint_survives_the_launcher_exec(self):
        runtime = object.__new__(StableRuntime)
        runtime.root = ROOT
        runtime.cfg = StableConfig(ROOT)
        runtime.run_dir = ROOT / "output" / "stable" / "runs" / "run_test"
        fingerprint = runtime.workload_fingerprint(find_workload("jdbc.prepared_leak"))
        command = runtime._jdbc_command()
        launcher = (ROOT / "suites" / "stable" / "assets" / "jdbc" / "run_prepared_leak.sh").read_text(encoding="utf-8")

        self.assertEqual(str(runtime.run_dir), fingerprint)
        self.assertEqual(["--run-dir", str(runtime.run_dir)], command[command.index("--run-dir"):command.index("--run-dir") + 2])
        self.assertIn('RUN_DIR="$2"', launcher)
        self.assertIn('exec java -Dfbasecman.stable.run_dir="$RUN_DIR"', launcher)

    def test_state_store_writes_json_atomically(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            store = StateStore(path); store.save({"status": "running"})
            self.assertEqual("running", store.load()["status"])
            self.assertEqual("running", json.loads(path.read_text())["status"])

    def test_status_observation_does_not_modify_stable_state(self):
        with TemporaryDirectory() as directory:
            state_file = Path(directory) / "state.json"
            store = StateStore(state_file)
            store.save({"status": "running", "run_id": "run_1"})
            before = state_file.read_text(encoding="utf-8")
            revision = store.load()["revision"]
            cfg = type("Config", (), {"state_file": state_file})()

            observed = refresh_status(cfg)
            after = state_file.read_text(encoding="utf-8")

        self.assertEqual("running", observed["status"])
        self.assertEqual(revision, observed["revision"])
        self.assertEqual(before, after)

    def test_report_reuses_the_config_saved_with_the_run(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            state_file = root / "state.json"
            run_dir = root / "run"
            run_dir.mkdir()
            StateStore(state_file).save({
                "status": "completed", "run_id": "run_1", "run_dir": str(run_dir),
                "started_at": 1, "workloads": {},
                "config_files": [str(ROOT / "suites" / "stable" / "assets" / "config" / "stable.smoke.yaml")],
                "environment_before": {"ok": True}, "environment_after": {"ok": True},
            })
            cfg = type("Config", (), {"root": ROOT, "state_file": state_file})()
            report = write_report_from_state(cfg)

            text = report.read_text(encoding="utf-8")

        self.assertIn("pgbench 时长: 20s", text)
        self.assertIn("JDBC 时长: 20s", text)
        self.assertIn("before={'ok': True}", text)

    def test_supervisor_marks_finished_workload_without_status_polling(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            log = root / "pgbench.log"
            log.write_text(
                "number of transactions actually processed: 10\n"
                "number of failed transactions: 0 (0.0%)\n", encoding="utf-8",
            )
            store = StateStore(root / "state.json")
            store.save({"status": "running", "run_id": "run_1", "run_dir": str(root),
                        "workloads": {"pgbench.balance": {
                            "pid": 999999, "status": "running", "log": str(log),
                            "fingerprint": str(root),
                        }}})

            self.assertTrue(_observe_workloads(store))
            item = store.load()["workloads"]["pgbench.balance"]

        self.assertEqual("completed", item["status"])
        self.assertTrue(item["result"]["ok"])

    def test_supervisor_claims_finalization_after_all_workloads_finish(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            log = root / "pgbench.log"
            log.write_text(
                "number of transactions actually processed: 10\n"
                "number of failed transactions: 0 (0.0%)\n", encoding="utf-8",
            )
            state_file = root / "state.json"
            StateStore(state_file).save({
                "status": "running", "run_id": "run_1", "run_dir": str(root),
                "workloads": {"pgbench.balance": {
                    "pid": 999999, "status": "running", "log": str(log),
                    "fingerprint": str(root),
                }},
            })
            with patch("suites.stable.supervisor._finalize") as finalize:
                self.assertEqual(0, supervise(ROOT, state_file, poll_interval=0))

            state = StateStore(state_file).load()

        finalize.assert_called_once()
        self.assertEqual("finalizing", state["status"])

    def test_final_route_verdict_uses_immutable_preflight_evidence(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = object.__new__(StableRuntime)
            runtime.root = root
            runtime.run_dir = root / "run"
            runtime.logs = runtime.run_dir / "logs"
            runtime.logs.mkdir(parents=True)
            runtime.product_log = runtime.logs / "fbasecman.log"
            runtime.product_log.write_text("no workload debug trace", encoding="utf-8")
            runtime.health = lambda: {"ok": True}
            runtime.capture_pg_log_windows = lambda since: {"directory": "", "findings": []}
            runtime.compress_pg_log_windows = lambda since: {"directory": "", "archives": [], "reports": []}
            state = {
                "status": "running", "started_at": 0,
                "workloads": {"pgbench.balance": {"status": "completed"}},
                "route_preflight": {"pgbench.balance": {"ok": True, "route": {"ok": True}}},
            }
            with patch("suites.stable.runtime.scan_logs", return_value=[]), \
                    patch("suites.stable.runtime.core_files", return_value=[]), \
                    patch("suites.stable.runtime.route_evidence", return_value={"pgbench.balance": {"ok": False}}):
                runtime.finalize_state(state)

        self.assertEqual("completed", state["status"])
        self.assertTrue(state["route_evidence"]["pgbench.balance"]["ok"])

    def test_pid_fingerprint_does_not_match_unrelated_process(self):
        self.assertFalse(managed_pid(os.getpid(), "/definitely/not/in/current/cmdline"))

    def test_log_scan_ignores_expected_readiness_probe_failure(self):
        with TemporaryDirectory() as directory:
            logs = Path(directory) / "logs"
            logs.mkdir()
            (logs / "wait_console_ready_01.log").write_text("FATAL: connection refused\n")
            (logs / "fbasecman.log").write_text("ERROR: unexpected\n")
            findings = scan_logs(directory)
        self.assertEqual(1, len(findings))
        self.assertIn("unexpected", findings[0])

    def test_pgbench_report_summary_does_not_copy_debug_trace(self):
        text = "pgbench: client 1 sending SELECT 1;\nnumber of transactions actually processed: 10\nnumber of failed transactions: 0 (0.000%)\ntps = 12.3\n"
        summary = workload_log_summary(text, "pgbench")
        self.assertNotIn("sending SELECT", summary)
        self.assertIn("transactions actually processed: 10", summary)

    def test_route_evidence_accepts_eligible_route_and_requires_balance_coverage(self):
        text = "\n".join((
            "(routing) matched rule(pg_220 mmrhint mmrhint ) with (null) routing type",
            "(routing) matched rule(pg_220 balance balance ) with (null) routing type",
            "(routing) matched rule(pg_230 balance balance ) with (null) routing type",
        ))
        evidence = route_evidence(text, ("pgbench.mmr_hint_long", "pgbench.balance"))
        self.assertTrue(evidence["pgbench.mmr_hint_long"]["ok"])
        self.assertTrue(evidence["pgbench.balance"]["ok"])

    def test_route_startup_pending_only_accepts_monitor_bootstrap_errors(self):
        self.assertTrue(route_startup_pending(
            "ERROR: fbasecman: no healthy write server is available"))
        self.assertTrue(route_startup_pending(
            "ERROR: group(mmrhint) have no node for write operation"))
        self.assertFalse(route_startup_pending("ERROR: authentication failed"))
        self.assertFalse(route_startup_pending("ERROR: syntax error at or near SELECT"))

    def test_runtime_for_state_does_not_create_a_new_run_directory(self):
        cfg = StableConfig(ROOT)
        with TemporaryDirectory() as directory:
            run_dir = Path(directory) / "run"
            run_dir.mkdir()
            runtime = runtime_for_state(cfg, {"run_id": "existing", "run_dir": str(run_dir)})
        self.assertEqual(run_dir, runtime.run_dir)

    def test_memory_plot_help_does_not_require_external_python_package(self):
        text = (ROOT / "tools" / "stable_cli.py").read_text(encoding="utf-8")
        self.assertIn("fbasecman_memory.svg", text)
        self.assertNotIn("import matplotlib", text)

    def test_documented_public_commands_are_exposed_by_cli(self):
        cli_help = parser().format_help()
        readme = (ROOT / "suites" / "stable" / "README.md").read_text(encoding="utf-8")
        for command in ("show", "doctor", "run", "render-conf", "reload", "start", "restart", "stop",
                        "status", "inspect", "recover", "top", "tui", "diagnose", "archive", "memory-plot", "pg-log-check",
                        "report", "clean"):
            self.assertIn("./stable.sh %s" % command, readme)
            self.assertIn(command, cli_help)

    def test_pgbench_does_not_request_per_command_latency_output(self):
        cfg = StableConfig(ROOT)
        runtime = object.__new__(__import__("suites.stable.runtime", fromlist=["StableRuntime"]).StableRuntime)
        runtime.cfg = cfg
        runtime.root = ROOT
        runtime.main_port = 26432
        runtime.write_port = 27432
        command = runtime._pgbench_command(find_workload("pgbench.mmr_hint_long"))
        self.assertNotIn("-r", command)
        self.assertEqual("run_pgbench.sh", Path(command[1]).name)

    def test_reload_status_toggle_runs_reload_churn_with_exact_rw_sql(self):
        runtime = StableRuntime(ROOT)
        runtime.run_dir.mkdir(parents=True, exist_ok=True)
        runtime.config_dir.mkdir(parents=True, exist_ok=True)
        command = runtime._pgbench_command(find_workload("pgbench.reload_status_toggle"))
        sql = (ROOT / "suites" / "stable" / "assets" / "pgbench" /
               "reload_rw_toggle.sql").read_text(encoding="utf-8").splitlines()

        self.assertEqual("run_reload_stress.sh", Path(command[1]).name)
        self.assertIn("pg_240", command)
        self.assertIn("-C", command)
        self.assertEqual("10", command[command.index("-c") + 1])
        self.assertEqual([
            "SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY;",
            "SET SESSION CHARACTERISTICS AS TRANSACTION READ WRITE;",
            "SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY;",
            "SET SESSION CHARACTERISTICS AS TRANSACTION READ WRITE;",
        ], sql)

    def test_pgbench_wrapper_preserves_result_lines_without_debug_trace(self):
        wrapper = ROOT / "suites" / "stable" / "assets" / "pgbench" / "run_pgbench.sh"
        text = wrapper.read_text(encoding="utf-8")
        self.assertIn("number of transactions actually processed:", text)
        self.assertIn("number of failed transactions:", text)
        self.assertNotIn("client 1 sending", text)

    def test_pg_log_archive_only_targets_closed_logs_from_this_run(self):
        script = pg_log_archive_script("/opt/pg", 10011, "postgres", 1234, "mmr1")
        self.assertIn("command -v lsof", script)
        self.assertIn("lsof -F n", script)
        self.assertIn('[ "$modified" -ge 1234 ]', script)
        self.assertIn("tar -czf", script)
        self.assertIn("--remove-files", script)
        self.assertIn("SKIPPED_OPEN_FILES", script)

    def test_pg_log_window_filters_records_before_run_start(self):
        script = pg_log_window_script("/opt/pg", 10011, "postgres", 1234, "mmr1")
        self.assertIn("run_start=$(date -d '@1234'", script)
        self.assertIn("awk -v start=\"$run_start\"", script)
        self.assertIn("keep = substr($0, 1, 19) >= start", script)

    def test_pg_log_archive_keeps_open_file_and_compresses_closed_file(self):
        with TemporaryDirectory() as directory:
            root = Path(directory); fake_bin = root / "bin"; fake_bin.mkdir()
            pg_bin = root / "pg" / "bin"; pg_bin.mkdir(parents=True)
            log_dir = root / "logs"; log_dir.mkdir()
            closed = log_dir / "closed.csv"; open_file = log_dir / "open.csv"
            closed.write_text("closed\n", encoding="utf-8"); open_file.write_text("open\n", encoding="utf-8")
            os.utime(str(closed), (1300, 1300)); os.utime(str(open_file), (1300, 1300))
            psql = pg_bin / "psql"
            psql.write_text(
                "#!/bin/sh\ncase \"$*\" in *data_directory*) printf '%s\\n' \"$TOP_DATA_DIR\" ;; *log_directory*) printf '%s\\n' \"$TOP_LOG_DIR\" ;; esac\n",
                encoding="utf-8")
            lsof = fake_bin / "lsof"
            lsof.write_text("#!/bin/sh\nprintf 'n%s\\n' \"$TOP_OPEN_FILE\"\n", encoding="utf-8")
            psql.chmod(0o755); lsof.chmod(0o755)
            env = os.environ.copy()
            env.update({"PATH": str(fake_bin) + os.pathsep + env["PATH"], "TOP_DATA_DIR": str(root),
                        "TOP_LOG_DIR": str(log_dir), "TOP_OPEN_FILE": str(open_file)})
            result = subprocess.run(["bash", "-c", pg_log_archive_script(root / "pg", 10011, "postgres", 1234, "mmr1")],
                                    env=env, universal_newlines=True, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, check=True)

            archives = list((log_dir / "stable_archive").glob("*.tar.gz"))
            self.assertIn("ARCHIVED_FILES=1", result.stdout)
            self.assertIn("SKIPPED_OPEN_FILES=1", result.stdout)
            self.assertFalse(closed.exists())
            self.assertTrue(open_file.exists())
            self.assertEqual(1, len(archives))
            listed = subprocess.check_output(["tar", "-tzf", str(archives[0])], universal_newlines=True)
            self.assertIn("closed.csv", listed)

    def test_cli_exposes_documented_commands(self):
        cli = parser()
        text = cli.format_help()
        for command in ("show", "run", "render-conf", "reload", "start", "restart", "stop", "status", "top", "tui",
                        "inspect", "recover", "diagnose", "archive", "memory-plot", "pg-log-check", "report", "clean"):
            self.assertIn(command, text)

    def test_top_renders_resource_and_workload_snapshot_without_extra_dependencies(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            cfg = type("Config", (), {"state_file": root / "state.json", "output_dir": root})()
            run_dir = root / "run"; (run_dir / "monitor").mkdir(parents=True); (run_dir / "logs").mkdir()
            StateStore(cfg.state_file).save({"status": "running", "run_id": "run_1", "run_dir": str(run_dir),
                                             "fbasecman_pid": 0, "monitor_pid": 0,
                                             "workloads": {"pgbench.balance": {"pid": 0, "status": "running", "returncode": None}}})
            (run_dir / "monitor" / "resources.csv").write_text(
                "timestamp,pid,rss_kb,vsz_kb,cpu_percent,threads,fd_count,read_bytes,write_bytes\n"
                "2026-01-01T00:00:00,1,100,200,3.5,4,5,6,7\n", encoding="utf-8")
            (run_dir / "logs" / "fbasecman.log").write_text("ready\n", encoding="utf-8")
            text = dashboard(cfg)
            stream = StringIO(); self.assertEqual(0, run_top(cfg, once=True, stream=stream))

        self.assertIn("rss=100 KB", text)
        self.assertIn("pgbench.balance", text)
        self.assertIn("ready", stream.getvalue())

    def test_tui_reports_missing_optional_dependencies(self):
        cfg = type("Config", (), {"state_file": ROOT / "output" / "stable" / "runtime" / "state.json"})()
        stream = StringIO()
        with patch("tools.stable_top._tui_python", return_value=None):
            self.assertEqual(1, run_tui(cfg, stream=stream))
        self.assertIn("Textual TUI requires", stream.getvalue())
