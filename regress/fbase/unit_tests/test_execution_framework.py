import os
import subprocess
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from framework.assertions import command_succeeds
from framework.context import TestContext
from framework.errors import SafetyError
from framework.environment import EnvironmentManager
from framework.fixtures import (FIXTURES, _isolated_cluster_root,
                                _register_isolated_mmr_instance,
                                _reclaim_stale_isolated_mmr_listeners,
                                _remove_stale_isolated_cluster_root,
                                _reserve_isolated_mmr_ports,
                                release_isolated_mmr_port_for_command)
from framework.postgres import PostgresClient
from framework.process import CommandRunner
from framework.runner import SuiteRunner
from framework.steps import StepExecutor, command_step


class FakeCommandRunner(object):
    def __init__(self, process):
        self.process = process
        self.commands = []

    def run(self, command, check=False, input_text=None, timeout=None):
        self.commands.append(command)
        if isinstance(self.process, list):
            return self.process.pop(0)
        return self.process


class PostgresClientTest(unittest.TestCase):
    def test_csv_result_is_structured_and_rendered_as_psql_table(self):
        process = subprocess.CompletedProcess([], 0, stdout=(
            "name,value\n"
            "first,__FBASE_REGRESS_NULL__\n"
            "second,\"with,comma\"\n"))
        runner = FakeCommandRunner(process)
        manager = SimpleNamespace(
            node=lambda unused: {"host": "127.0.0.1", "port": 15432},
            binary=lambda name: "/pg/bin/%s" % name,
        )
        result = PostgresClient(manager, runner).execute(
            "primary", "postgres", "postgres", "SELECT 1", structured=True)
        self.assertEqual(result.columns, ["name", "value"])
        self.assertEqual(result.rows, [["first", None], ["second", "with,comma"]])
        self.assertIn(" name   | value", result.display_output)
        self.assertIn(" first  |", result.display_output)
        self.assertIn("(2 rows)", result.display_output)
        self.assertIn("--csv", runner.commands[0])

    def test_csv_result_ignores_psql_warning_before_the_result(self):
        process = subprocess.CompletedProcess([], 0, stdout=(
            "WARNING:  01000: could not check the time difference with self.\n"
            "LOCATION:  show_node_info, fdd_check_node_info.c:1197\n"
            "count,healthy\n"
            "3,true\n"))
        runner = FakeCommandRunner(process)
        manager = SimpleNamespace(
            node=lambda unused: {"host": "127.0.0.1", "port": 15432},
            binary=lambda name: "/pg/bin/%s" % name,
        )
        result = PostgresClient(manager, runner).execute(
            "primary", "postgres", "postgres", "SELECT 1", structured=True)
        self.assertEqual(result.rows, [["3", "true"]])
        self.assertIn("WARNING:  01000", result.display_output)
        self.assertIn(" count | healthy", result.display_output)

    def test_csv_result_ignores_indented_warning_continuation(self):
        process = subprocess.CompletedProcess([], 0, stdout=(
            "WARNING:  01000: connection failed\n"
            "\tIs the server running?.\n"
            "LOCATION:  fdd_get_connect, fdd_logical.c:135\n"
            "state,count\n"
            "CONNECT_FAIL,2\n"))
        runner = FakeCommandRunner(process)
        manager = SimpleNamespace(
            node=lambda unused: {"host": "127.0.0.1", "port": 15432},
            binary=lambda name: "/pg/bin/%s" % name,
        )
        result = PostgresClient(manager, runner).execute(
            "primary", "postgres", "postgres", "SELECT 1", structured=True)
        self.assertEqual(result.columns, ["state", "count"])
        self.assertEqual(result.rows, [["CONNECT_FAIL", "2"]])
        self.assertIn("Is the server running?", result.display_output)

    def test_setting_check_uses_show_and_structured_result(self):
        runner = FakeCommandRunner([
            subprocess.CompletedProcess([], 0, stdout=(
                "config_file,line,configuration,applied,error\n"
                "/pg/data/postgresql.conf,12,fdb.separate_user = 'on',t,\n")),
            subprocess.CompletedProcess([], 0, stdout=(
                "fdb.separate_user\n"
                "on\n")),
        ])
        manager = SimpleNamespace(
            node=lambda unused: {"host": "127.0.0.1", "port": 15432},
            binary=lambda name: "/pg/bin/%s" % name,
        )
        detail = PostgresClient(manager, runner).check_setting("primary", {
            "name": "fdb.separate_user", "equals": "on",
            "purpose": "启用三权分立",
        })
        self.assertTrue(detail["matched"])
        self.assertEqual(detail["sql"], "SHOW fdb.separate_user")
        self.assertIn(" fdb.separate_user", detail["output"])
        self.assertIn("FROM pg_file_settings", detail["config_sql"])
        self.assertIn("/pg/data/postgresql.conf", detail["config_output"])
        self.assertEqual(len(runner.commands), 2)

    def test_table_alignment_counts_chinese_as_double_width(self):
        process = subprocess.CompletedProcess([], 0, stdout=(
            "name,value\n"
            "中文,1\n"
            "a,2\n"))
        runner = FakeCommandRunner(process)
        manager = SimpleNamespace(
            node=lambda unused: {"host": "127.0.0.1", "port": 15432},
            binary=lambda name: "/pg/bin/%s" % name,
        )
        result = PostgresClient(manager, runner).execute(
            "primary", "postgres", "postgres", "SELECT 1", structured=True)
        self.assertIn(" 中文 | 1", result.display_output)
        self.assertIn(" a    | 2", result.display_output)


class ContextCleanupTest(unittest.TestCase):
    def test_logical_replication_objects_remove_subscription_before_publication(self):
        class FakePostgres(object):
            def __init__(self):
                self.commands = []

            def execute_checked(self, node, user, database, sql):
                self.commands.append((node, sql))

        class FakeContext(object):
            def __init__(self):
                self.postgres = FakePostgres()
                self.cleanups = []

            def resolve_node(self, selector):
                return selector

            def add_cleanup(self, title, callback, priority=0):
                self.cleanups.append((title, callback, priority))

        context = FakeContext()
        FIXTURES.setup_all(context, [{
            "type": "logical_replication_objects", "source": "source", "target": "target",
            "source_table": "case_source", "target_table": "case_target",
            "publication": "case_pub", "subscription": "case_sub",
        }])
        context.cleanups[0][1]()
        self.assertEqual(context.postgres.commands[0],
                         ("target", 'DROP SUBSCRIPTION IF EXISTS "case_sub"'))
        self.assertEqual(context.postgres.commands[1],
                         ("source", 'DROP PUBLICATION IF EXISTS "case_pub"'))

    def test_mmr_global_sequences_empty_rejects_user_metadata(self):
        class FakePostgres(object):
            def scalar(self, unused_node, unused_database, unused_sql, user="postgres"):
                return "1"

        class FakeContext(object):
            def __init__(self):
                self.postgres = FakePostgres()

            def resolve_node(self, selector):
                return selector

        with self.assertRaises(SafetyError):
            FIXTURES.setup_all(FakeContext(), [{
                "type": "mmr_global_sequences_empty", "nodes": ["mmr1"],
            }])

    def test_mmr_global_sequence_probe_deletes_metadata_before_sequences(self):
        class FakePostgres(object):
            def __init__(self):
                self.commands = []

            def scalar(self, node, database, sql, user="postgres"):
                self.commands.append((node, sql))
                if "to_regclass" in sql:
                    return "true"
                return "true"

            def execute_checked(self, node, user, database, sql):
                self.commands.append((node, sql))

        class FakeContext(object):
            def __init__(self):
                self.postgres = FakePostgres()
                self.cleanups = []

            def resolve_node(self, selector):
                return selector

            def add_cleanup(self, title, callback, priority=0):
                self.cleanups.append((title, callback, priority))

        context = FakeContext()
        # The setup probe requires no existing sequence, then cleanup observes one.
        calls = ["false", "false", "true", "true", "true"]

        def scalar(node, database, sql, user="postgres"):
            context.postgres.commands.append((node, sql))
            return calls.pop(0)

        context.postgres.scalar = scalar
        FIXTURES.setup_all(context, [{
            "type": "mmr_global_sequence_probe", "node": "mmr1",
            "nodes": ["mmr1", "mmr2"], "name": "case_sequence",
        }])
        context.cleanups[0][1]()
        cleanup_sql = [sql for unused_node, sql in context.postgres.commands
                       if sql.startswith("SELECT fdd.delete") or sql.startswith("DROP SEQUENCE")]
        self.assertTrue(cleanup_sql[0].startswith("SELECT fdd.delete_global_seq"))
        self.assertEqual(cleanup_sql[1:], [
            'DROP SEQUENCE IF EXISTS public."case_sequence"',
            'DROP SEQUENCE IF EXISTS public."case_sequence"',
        ])

    def test_mmr_global_sequence_probe_supports_a_nonpublic_schema(self):
        class FakePostgres(object):
            def scalar(self, node, database, sql, user="postgres"):
                return "false"

            def execute_checked(self, node, user, database, sql):
                self.sql = sql

        class FakeContext(object):
            def __init__(self):
                self.postgres = FakePostgres()
                self.cleanups = []

            def resolve_node(self, selector):
                return selector

            def add_cleanup(self, title, callback, priority=0):
                self.cleanups.append((title, callback, priority))

        context = FakeContext()
        FIXTURES.setup_all(context, [{
            "type": "mmr_global_sequence_probe", "node": "mmr1", "nodes": ["mmr1"],
            "schema": "case_schema", "name": "case_sequence",
        }])
        context.cleanups[0][1]()
        self.assertEqual(context.postgres.sql,
                         'DROP SEQUENCE IF EXISTS "case_schema"."case_sequence"')

    def test_mmr_remote_sql_tables_cleans_each_table_on_each_member(self):
        class FakePostgres(object):
            def __init__(self):
                self.commands = []

            def scalar(self, node, database, sql, user="postgres"):
                self.commands.append((node, sql))
                return "false"

            def execute_checked(self, node, user, database, sql):
                self.commands.append((node, sql))

        class FakeContext(object):
            def __init__(self):
                self.postgres = FakePostgres()
                self.cleanups = []

            def resolve_node(self, selector):
                return selector

            def add_cleanup(self, title, callback, priority=0):
                self.cleanups.append((title, callback, priority))

        context = FakeContext()
        FIXTURES.setup_all(context, [{
            "type": "mmr_remote_sql_tables", "nodes": ["mmr1", "mmr2"],
            "tables": ["case_one", "case_two"],
        }])
        context.cleanups[0][1]()
        drops = [sql for unused_node, sql in context.postgres.commands
                 if sql.startswith("DROP TABLE")]
        self.assertEqual(len(drops), 4)
        self.assertIn('DROP TABLE IF EXISTS public."case_one"', drops)

    def test_mmr_global_failover_guard_restores_with_global_udf(self):
        class FakePostgres(object):
            def scalar(self, node, database, sql, user="postgres"):
                return "true"

            def execute_checked(self, node, user, database, sql):
                self.sql = sql

        class FakeContext(object):
            def __init__(self):
                self.postgres = FakePostgres()
                self.cleanups = []

            def resolve_node(self, selector):
                return selector

            def add_cleanup(self, title, callback, priority=0):
                self.cleanups.append((title, callback, priority))

        context = FakeContext()
        FIXTURES.setup_all(context, [{
            "type": "mmr_global_failover_guard", "node": "mmr1", "nodes": ["mmr1", "mmr2"],
            "node_name": "mmr2",
        }])
        context.cleanups[0][1]()
        self.assertEqual(
            context.postgres.sql,
            "SELECT fdd.alter_node_failover('mmr2', true, true)")

    def test_mmr_streaming_parallel_guard_restores_with_product_udf(self):
        class FakePostgres(object):
            def __init__(self):
                self.sql = []

            def scalar(self, node, database, sql, user="postgres"):
                if "fdd.show_node_info" in sql:
                    return "0"
                return "2|true"

            def execute_checked(self, node, user, database, sql):
                self.sql.append((node, sql))

        class FakeContext(object):
            def __init__(self):
                self.postgres = FakePostgres()
                self.cleanups = []

            def resolve_node(self, selector):
                return selector

            def add_cleanup(self, title, callback, priority=0):
                self.cleanups.append((title, callback, priority))

        context = FakeContext()
        FIXTURES.setup_all(context, [{
            "type": "mmr_streaming_parallel_guard", "node": "mmr1",
            "nodes": ["mmr1", "mmr2"], "node_name": "mmr2",
        }])
        context.cleanups[0][1]()
        self.assertEqual(context.postgres.sql[0], (
            "mmr1",
            "SELECT fdd.alter_node_info('streaming', 'mmr2', 'parallel', true)"))
        self.assertEqual([node for node, unused_sql in context.postgres.sql[1:]],
                         ["mmr1", "mmr2"])
        self.assertTrue(all("ALTER SUBSCRIPTION" in sql
                            for unused_node, sql in context.postgres.sql[1:]))

    def test_mmr_subscriptions_enabled_guard_restores_with_product_udf(self):
        class FakePostgres(object):
            def scalar(self, node, database, sql, user="postgres"):
                return "2|true"

            def execute_checked(self, node, user, database, sql):
                self.sql = sql

        class FakeContext(object):
            def __init__(self):
                self.postgres = FakePostgres()
                self.cleanups = []

            def resolve_node(self, selector):
                return selector

            def add_cleanup(self, title, callback, priority=0):
                self.cleanups.append((title, callback, priority))

        context = FakeContext()
        FIXTURES.setup_all(context, [{
            "type": "mmr_subscriptions_enabled_guard", "node": "mmr1",
        }])
        context.cleanups[0][1]()
        self.assertEqual(context.postgres.sql, "SELECT fdd.alter_subscription_enable()")

    def test_mmr_sub_repsets_guard_restores_original_array_with_product_udf(self):
        class FakePostgres(object):
            def scalar(self, node, database, sql, user="postgres"):
                return "'{fbase_regress_mmr}'"

            def execute_checked(self, node, user, database, sql):
                self.sql = sql

        class FakeContext(object):
            def __init__(self):
                self.postgres = FakePostgres()
                self.cleanups = []

            def resolve_node(self, selector):
                return selector

            def add_cleanup(self, title, callback, priority=0):
                self.cleanups.append((title, callback, priority))

        context = FakeContext()
        FIXTURES.setup_all(context, [{"type": "mmr_sub_repsets_guard", "node": "mmr1"}])
        self.assertEqual(context.cleanups[0][2], 200)
        context.cleanups[0][1]()
        self.assertEqual(context.postgres.sql,
                         "SELECT fdd.check_and_adjust_sub_repsets('{fbase_regress_mmr}'::text[])")

    def test_mmr_node_failover_guard_restores_original_value_with_udf(self):
        class FakePostgres(object):
            def __init__(self):
                self.commands = []

            def scalar(self, node, database, sql, user="postgres"):
                self.commands.append(sql)
                return "true"

            def execute_checked(self, node, user, database, sql):
                self.commands.append(sql)

        class FakeContext(object):
            def __init__(self):
                self.postgres = FakePostgres()
                self.cleanups = []

            def resolve_node(self, selector):
                return selector

            def add_cleanup(self, title, callback, priority=0):
                self.cleanups.append((title, callback, priority))

        context = FakeContext()
        FIXTURES.setup_all(context, [{
            "type": "mmr_node_failover_guard", "node": "mmr1", "node_name": "mmr1",
        }])
        context.cleanups[0][1]()
        self.assertIn("fdd.alter_node_failover('mmr1', true, false)",
                      context.postgres.commands[-1])

    def test_mmr_async_set_mode_recovery_calls_product_udf_after_cleanup(self):
        class FakePostgres(object):
            def __init__(self):
                self.commands = []

            def execute_checked(self, node, user, database, sql):
                self.commands.append((node, sql))

        class FakeContext(object):
            def __init__(self):
                self.postgres = FakePostgres()
                self.cleanups = []

            def resolve_node(self, selector):
                return selector

            def add_cleanup(self, title, callback, priority=0):
                self.cleanups.append((title, callback, priority))

        context = FakeContext()
        FIXTURES.setup_all(context, [{
            "type": "mmr_async_set_mode_recovery", "nodes": ["mmr1", "mmr2"],
        }])
        self.assertEqual(context.cleanups[0][2], -100)
        context.cleanups[0][1]()
        self.assertEqual(context.postgres.commands, [
            ("mmr1", "SELECT fdd.check_async_record()"),
            ("mmr2", "SELECT fdd.check_async_record()"),
        ])

    def test_mmr_check_node_conf_empty_cleans_all_reserved_nodes(self):
        class FakePostgres(object):
            def __init__(self):
                self.commands = []

            def scalar(self, node, database, sql, user="postgres"):
                self.commands.append((node, sql))
                return "0"

            def execute_checked(self, node, user, database, sql):
                self.commands.append((node, sql))

        class FakeContext(object):
            def __init__(self):
                self.postgres = FakePostgres()
                self.cleanups = []

            def resolve_node(self, selector):
                return selector

            def add_cleanup(self, title, callback, priority=0):
                self.cleanups.append((title, callback, priority))

        context = FakeContext()
        FIXTURES.setup_all(context, [{
            "type": "mmr_check_node_conf_empty", "nodes": ["mmr1", "mmr2"],
        }])
        self.assertEqual(len(context.cleanups), 1)
        context.cleanups[0][1]()
        deletes = [item for item in context.postgres.commands
                   if item[1] == "TRUNCATE TABLE fdd.mmr_check_node_conf"]
        self.assertEqual(deletes, [
            ("mmr1", "TRUNCATE TABLE fdd.mmr_check_node_conf"),
            ("mmr2", "TRUNCATE TABLE fdd.mmr_check_node_conf"),
        ])

    def test_mmr_node_state_guard_restores_original_state_after_case_failure(self):
        class FakePostgres(object):
            def __init__(self):
                self.commands = []

            def scalar(self, node, database, sql, user="postgres"):
                self.commands.append(sql)
                return "ACTIVE"

            def execute_checked(self, node, user, database, sql):
                self.commands.append(sql)

        class FakeContext(object):
            def __init__(self):
                self.postgres = FakePostgres()
                self.cleanups = []

            def resolve_node(self, selector):
                self.selector = selector
                return "mmr1_primary"

            def add_cleanup(self, title, callback, priority=0):
                self.cleanups.append((title, callback, priority))

        context = FakeContext()
        FIXTURES.setup_all(context, [{
            "type": "mmr_node_state_guard", "node": "mmr:mmr1", "node_id": 2,
        }])
        self.assertEqual(context.selector, "mmr:mmr1")
        self.assertEqual(len(context.cleanups), 1)
        context.cleanups[0][1]()
        self.assertIn("WHERE node_id = 2", context.postgres.commands[0])
        self.assertIn("SET node_state = 'ACTIVE'::fdd.mmr_node_state",
                      context.postgres.commands[-1])

    def test_context_expands_run_id_in_fixture_and_step_values(self):
        with tempfile.TemporaryDirectory() as root:
            context = TestContext(
                SimpleNamespace(), SimpleNamespace(), {"id": "test"}, Path(root),
                run_id="run_20260715_abcdef")
            self.assertEqual(
                context.expand("/tmp/fbase_regress_{run_id}"),
                "/tmp/fbase_regress_run_20260715_abcdef")
            self.assertEqual(
                context.expand({"argv": ["mkdir", "{run_id}"]}),
                {"argv": ["mkdir", "run_20260715_abcdef"]})

    def test_cleanup_is_lifo_and_continues_after_error(self):
        with tempfile.TemporaryDirectory() as root:
            manager = SimpleNamespace()
            context = TestContext(
                SimpleNamespace(), manager, {"id": "test"}, Path(root))
            calls = []
            context.add_cleanup("first", lambda: calls.append("first"))

            def broken():
                calls.append("broken")
                raise RuntimeError("cleanup error")

            context.add_cleanup("broken", broken)
            context.add_cleanup("last", lambda: calls.append("last"))
            errors = context.cleanup()
            self.assertEqual(calls, ["last", "broken", "first"])
            self.assertEqual(errors, ["broken: cleanup error"])

    def test_cleanup_higher_priority_runs_before_lifo_cleanups(self):
        with tempfile.TemporaryDirectory() as root:
            context = TestContext(
                SimpleNamespace(), SimpleNamespace(), {"id": "test"}, Path(root))
            calls = []
            context.add_cleanup("resource", lambda: calls.append("resource"))
            context.add_cleanup("setting", lambda: calls.append("setting"), priority=100)
            context.cleanup()
            self.assertEqual(calls, ["setting", "resource"])

    def test_settings_fixture_registers_restore_before_change_can_fail(self):
        class FakePostgres(object):
            def __init__(self):
                self.sql = []

            def scalar(self, node, database, sql, user="postgres"):
                return "64MB"

            def execute(self, node, user, database, sql, structured=False):
                return SimpleNamespace(returncode=0, rows=[["32MB"]], output="")

            def execute_checked(self, node, user, database, sql):
                self.sql.append(sql)
                if "128MB" in sql:
                    raise RuntimeError("set failed")

        class FakeContext(object):
            def __init__(self):
                self.postgres = FakePostgres()
                self.reload_options = []
                self.manager = SimpleNamespace(
                    reload=lambda **kwargs: self.reload_options.append(kwargs))
                self.cleanups = []

            def resolve_node(self, selector):
                return "primary"

            def add_cleanup(self, title, callback, priority=0):
                self.cleanups.append((priority, title, callback))

        context = FakeContext()
        with self.assertRaisesRegex(RuntimeError, "set failed"):
            FIXTURES.setup_all(context, [{
                "type": "settings", "values": {"shared_buffers": "128MB"},
            }])
        self.assertEqual(len(context.cleanups), 1)
        context.cleanups[0][2]()
        self.assertIn("ALTER SYSTEM SET shared_buffers = '32MB'", context.postgres.sql)
        self.assertEqual(context.reload_options, [{"quiet": True}])

    def test_settings_fixture_resets_setting_without_original_auto_override(self):
        class FakePostgres(object):
            def __init__(self):
                self.sql = []

            def scalar(self, node, database, sql, user="postgres"):
                return "64MB"

            def execute(self, node, user, database, sql, structured=False):
                return SimpleNamespace(returncode=0, rows=[], output="")

            def execute_checked(self, node, user, database, sql):
                self.sql.append(sql)

            def check_setting(self, node, spec):
                return {"matched": True, "actual": spec["equals"]}

        class FakeContext(object):
            def __init__(self):
                self.postgres = FakePostgres()
                self.cleanups = []
                self.values = {}
                self.manager = SimpleNamespace(reload=lambda **kwargs: None)

            def resolve_node(self, selector):
                return "primary"

            def add_cleanup(self, title, callback, priority=0):
                self.cleanups.append((priority, title, callback))

        context = FakeContext()
        FIXTURES.setup_all(context, [{
            "type": "settings", "values": {"shared_buffers": "128MB"},
        }])
        context.cleanups[0][2]()
        self.assertIn("ALTER SYSTEM RESET shared_buffers", context.postgres.sql)

    def test_mac_policy_fixture_registers_policy_cleanup_before_table_cleanup(self):
        class FakePostgres(object):
            def __init__(self):
                self.sql = []

            def execute_checked(self, node, user, database, sql):
                self.sql.append((node, user, database, sql))

            def scalar(self, node, database, sql, user="postgres"):
                return "1"

        class FakeContext(object):
            def __init__(self):
                self.postgres = FakePostgres()
                self.cleanups = []

            def resolve_node(self, selector):
                return "primary"

            def add_cleanup(self, title, callback, priority=0):
                self.cleanups.append((priority, title, callback))

        context = FakeContext()
        FIXTURES.setup_all(context, [{
            "type": "mac_policy", "table": "case_table", "policy": "case_policy",
            "column": "case_label",
        }])
        self.assertIn("CREATE TABLE \"public\".\"case_table\"", context.postgres.sql[0][3])
        self.assertIn("create_policy('case_policy', 'case_label')", context.postgres.sql[1][3])
        self.assertIn("create_level('case_policy', 'L1', 10)", context.postgres.sql[2][3])
        self.assertIn("create_compartment('case_policy', 'C1', 10)", context.postgres.sql[3][3])
        self.assertIn("create_label('case_policy', 'L1:C1', 11)", context.postgres.sql[4][3])
        self.assertIn("apply_table_policy('case_policy', 'public', 'case_table', false)",
                      context.postgres.sql[5][3])
        context.cleanups[2][2]()
        context.cleanups[1][2]()
        context.cleanups[0][2]()
        self.assertIn("remove_table_policy('case_policy', 'public', 'case_table', true)",
                      context.postgres.sql[6][3])
        self.assertIn("drop_policy('case_policy', true)", context.postgres.sql[7][3])
        self.assertIn("DROP TABLE IF EXISTS \"public\".\"case_table\"", context.postgres.sql[8][3])

    def test_certificate_store_fixture_checks_only_active_metadata_tables(self):
        class FakePostgres(object):
            def __init__(self):
                self.sql = []

            def scalar(self, node, database, sql):
                self.sql.append(sql)
                return "0"

        context = SimpleNamespace(
            postgres=FakePostgres(),
            resolve_node=lambda selector: "primary",
        )
        FIXTURES.setup_all(context, ["certificate_store_empty"])
        self.assertEqual(len(context.postgres.sql), 2)
        self.assertTrue(all(
            "certs_info_bak" not in sql and "key_meta_data_bak" not in sql and
            "certs_info_achive" not in sql
            for sql in context.postgres.sql))

    def test_isolated_mmr_fixture_reclaims_stale_framework_listener(self):
        root = Path(tempfile.mkdtemp(prefix="fbase_regress_stale_mmr_"))
        data_dir = root / "node1"
        data_dir.mkdir()
        (data_dir / "PG_VERSION").write_text("15\n", encoding="utf-8")

        class FakeContext(object):
            def __init__(self):
                self.case = {"steps": [{
                    "type": "command",
                    "argv": ["sh", "-ec", "initdb -D %s; port = 16666" % data_dir],
                }]}
                self.command_runner = FakeCommandRunner([
                    subprocess.CompletedProcess([], 0, stdout="4321\n"),
                    subprocess.CompletedProcess([], 0, stdout=""),
                    subprocess.CompletedProcess([], 0, stdout=""),
                ])
                self.manager = SimpleNamespace(binary=lambda name: "/pg/bin/%s" % name)

            def expand(self, value):
                return value

        context = FakeContext()
        try:
            with patch("framework.fixtures._postgres_data_dir", return_value=data_dir):
                _reclaim_stale_isolated_mmr_listeners(context)
            self.assertFalse(data_dir.exists())
            self.assertEqual(context.command_runner.commands[0], [
                "lsof", "-nP", "-t", "-iTCP:16666", "-sTCP:LISTEN"])
            self.assertEqual(context.command_runner.commands[1], [
                "/pg/bin/pg_ctl", "-D", str(data_dir), "stop", "-m", "immediate"])
        finally:
            shutil.rmtree(str(root), ignore_errors=True)

    def test_isolated_mmr_fixture_refuses_non_framework_listener(self):
        class FakeContext(object):
            case = {"steps": [{
                "type": "command",
                "argv": ["sh", "-ec", "initdb -D /tmp/fbase_regress_test/node1; port = 16666"],
            }]}
            command_runner = FakeCommandRunner(
                subprocess.CompletedProcess([], 0, stdout="4321\n"))
            manager = SimpleNamespace(binary=lambda name: "/pg/bin/%s" % name)

            def expand(self, value):
                return value

        with patch("framework.fixtures._postgres_data_dir", return_value=Path("/var/lib/postgresql/data")):
            with self.assertRaisesRegex(SafetyError, "非本框架隔离实例"):
                _reclaim_stale_isolated_mmr_listeners(FakeContext())

    def test_isolated_cluster_cleanup_waits_for_mmr_port_release(self):
        root = Path(tempfile.mkdtemp(prefix="fbase_regress_cleanup_mmr_"))
        node = root / "node1"
        node.mkdir()
        (node / "PG_VERSION").write_text("15\n", encoding="utf-8")
        output_dir = Path(tempfile.mkdtemp(prefix="fbase_regress_output_"))

        class FakeContext(object):
            def __init__(self):
                self.case = {"steps": [{
                    "type": "command",
                    "argv": ["sh", "-ec", "initdb -D %s; port = 16667" % node],
                }]}
                self.command_runner = FakeCommandRunner(
                    subprocess.CompletedProcess([], 0, stdout=""))
                self.manager = SimpleNamespace(binary=lambda name: "/pg/bin/%s" % name)
                self.output_dir = output_dir
                self.cleanups = []

            def expand(self, value):
                return value

            def add_cleanup(self, title, callback, priority=0):
                self.cleanups.append((title, callback, priority))

        context = FakeContext()
        try:
            _isolated_cluster_root(context, {"data_dir": str(root)},
                                   "test", str(root), "cleanup")
            root.mkdir()
            node.mkdir()
            (node / "PG_VERSION").write_text("15\n", encoding="utf-8")
            context.command_runner.commands = []

            def verify_wait(unused_context):
                self.assertTrue(root.exists())

            with patch("framework.fixtures._wait_for_isolated_mmr_ports_free",
                       side_effect=verify_wait) as wait_for_ports:
                context.cleanups[0][1]()
            wait_for_ports.assert_called_once_with(context)
            self.assertFalse(root.exists())
        finally:
            shutil.rmtree(str(root), ignore_errors=True)
            shutil.rmtree(str(output_dir), ignore_errors=True)

    def test_isolated_cluster_cleanup_stops_root_data_directory(self):
        root = Path(tempfile.mkdtemp(prefix="fbase_regress_root_mmr_"))
        (root / "PG_VERSION").write_text("15\n", encoding="utf-8")
        output_dir = Path(tempfile.mkdtemp(prefix="fbase_regress_output_"))

        class FakeContext(object):
            def __init__(self):
                self.case = {"steps": []}
                self.command_runner = FakeCommandRunner(
                    subprocess.CompletedProcess([], 0, stdout=""))
                self.manager = SimpleNamespace(binary=lambda name: "/pg/bin/%s" % name)
                self.output_dir = output_dir
                self.cleanups = []

            def expand(self, value):
                return value

            def add_cleanup(self, title, callback, priority=0):
                self.cleanups.append((title, callback, priority))

        context = FakeContext()
        try:
            _isolated_cluster_root(context, {"data_dir": str(root)},
                                   "test", str(root), "cleanup")
            root.mkdir()
            (root / "PG_VERSION").write_text("15\n", encoding="utf-8")
            context.command_runner.commands = []
            with patch("framework.fixtures._wait_for_isolated_mmr_ports_free"):
                context.cleanups[0][1]()
            self.assertEqual(context.command_runner.commands[0], [
                "/pg/bin/pg_ctl", "-D", str(root), "stop", "-m", "immediate"])
            self.assertFalse(root.exists())
        finally:
            shutil.rmtree(str(root), ignore_errors=True)
            shutil.rmtree(str(output_dir), ignore_errors=True)

    def test_isolated_mmr_port_reservation_releases_only_started_node(self):
        import socket

        selector = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        selector.bind(("127.0.0.1", 0))
        port = selector.getsockname()[1]
        selector.close()
        data_dir = "/tmp/fbase_regress_reservation/node1"

        class FakeContext(object):
            case = {"steps": [{
                "type": "command",
                "argv": ["sh", "-ec", "initdb -D %s -U postgres; port = %s" %
                         (data_dir, port)],
            }]}

            def __init__(self):
                self.values = {}
                self.cleanups = []

            def expand(self, value):
                if isinstance(value, list):
                    return [self.expand(item) for item in value]
                if isinstance(value, str):
                    mapping = self.values.get("isolated_mmr_port_mapping") or {}
                    for declared, allocated in mapping.items():
                        value = value.replace("port = %s" % declared,
                                              "port = %s" % allocated)
                    return value
                return value

            def add_cleanup(self, title, callback, priority=0):
                self.cleanups.append((title, callback, priority))

        context = FakeContext()
        _reserve_isolated_mmr_ports(context)
        allocated = int(context.values["isolated_mmr_port_mapping"][str(port)])
        self.assertNotEqual(allocated, port)
        occupied_probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        probe = None
        try:
            with self.assertRaises(OSError):
                occupied_probe.bind(("127.0.0.1", allocated))
            release_isolated_mmr_port_for_command(
                context, ["sh", "-ec", "pg_ctl -D %s -w start" % data_dir])
            probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            probe.bind(("127.0.0.1", allocated))
        finally:
            if probe is not None:
                probe.close()
            occupied_probe.close()
            for unused_title, cleanup, unused_priority in context.cleanups:
                cleanup()

    def test_registered_isolated_port_releases_for_split_start_steps(self):
        import socket

        selector = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        selector.bind(("127.0.0.1", 0))
        declared_port = str(selector.getsockname()[1])
        selector.close()
        data_dir = "/tmp/fbase_regress_reservation/split_node"

        class FakeContext(object):
            case = {"steps": []}

            def __init__(self):
                self.values = {}
                self.cleanups = []

            def expand(self, value):
                return value

            def add_cleanup(self, title, callback, priority=0):
                self.cleanups.append((title, callback, priority))

        context = FakeContext()
        _reserve_isolated_mmr_ports(context, [declared_port])
        allocated_port = context.values["isolated_mmr_port_mapping"][declared_port]
        _register_isolated_mmr_instance(context, allocated_port, data_dir)
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        released_probe = None
        try:
            with self.assertRaises(OSError):
                probe.bind(("127.0.0.1", int(allocated_port)))
            probe.close()
            probe = None
            release_isolated_mmr_port_for_command(
                context, ["sh", "-ec", "pg_ctl -D %s -w start" % data_dir])
            released_probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            released_probe.bind(("127.0.0.1", int(allocated_port)))
        finally:
            if released_probe is not None:
                released_probe.close()
            if probe is not None:
                probe.close()
            for unused_title, cleanup, unused_priority in context.cleanups:
                cleanup()

    def test_basebackup_listener_port_is_reserved_and_released(self):
        import socket

        standby = "/tmp/fbase_regress_reservation/standby"
        declared_port = "15522"

        class FakeContext(object):
            case = {"steps": [{
                "type": "command",
                "argv": ["sh", "-ec", (
                    "pg_basebackup -h 127.0.0.1 -p 15521 -D %s -R; "
                    "printf 'port = %s\\n' >> %s/postgresql.conf; "
                    "pg_ctl -D %s -w start" %
                    (standby, declared_port, standby, standby))],
            }]}

            def __init__(self):
                self.values = {}
                self.cleanups = []

            def expand(self, value):
                if isinstance(value, list):
                    return [self.expand(item) for item in value]
                if isinstance(value, str):
                    mapping = self.values.get("isolated_mmr_port_mapping") or {}
                    for declared, allocated in mapping.items():
                        value = value.replace("port = %s" % declared,
                                              "port = %s" % allocated)
                    return value
                return value

            def add_cleanup(self, title, callback, priority=0):
                self.cleanups.append((title, callback, priority))

        context = FakeContext()
        _reserve_isolated_mmr_ports(context)
        allocated_port = context.values["isolated_mmr_port_mapping"][declared_port]
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        released_probe = None
        try:
            with self.assertRaises(OSError):
                probe.bind(("127.0.0.1", int(allocated_port)))
            probe.close()
            probe = None
            release_isolated_mmr_port_for_command(
                context, context.expand(context.case["steps"][0]["argv"]))
            released_probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            released_probe.bind(("127.0.0.1", int(allocated_port)))
        finally:
            if released_probe is not None:
                released_probe.close()
            if probe is not None:
                probe.close()
            for unused_title, cleanup, unused_priority in context.cleanups:
                cleanup()

    def test_escaped_newline_listener_ports_are_reserved(self):
        class FakeContext(object):
            case = {"steps": [{
                "type": "command",
                "argv": ["sh", "-ec", (
                    "initdb -D /tmp/fbase_regress_reservation/a; "
                    "printf '\\nport = 15513\\n' >> "
                    "/tmp/fbase_regress_reservation/a/postgresql.conf")],
            }, {
                "type": "command",
                "argv": ["sh", "-ec", (
                    "initdb -D /tmp/fbase_regress_reservation/b; "
                    "printf '\\nport = 15514\\n' >> "
                    "/tmp/fbase_regress_reservation/b/postgresql.conf")],
            }]}

            def __init__(self):
                self.values = {}
                self.cleanups = []

            def expand(self, value):
                return value

            def add_cleanup(self, title, callback, priority=0):
                self.cleanups.append((title, callback, priority))

        context = FakeContext()
        _reserve_isolated_mmr_ports(context)
        self.assertEqual(set(context.values["isolated_mmr_port_mapping"]),
                         {"15513", "15514"})
        for unused_title, cleanup, unused_priority in context.cleanups:
            cleanup()

    def test_unregistered_isolated_start_is_rejected(self):
        class FakeContext(object):
            values = {"isolated_mmr_port_reservations": {"20000": object()}}
            case = {"steps": []}

            def expand(self, value):
                return value

        with self.assertRaisesRegex(SafetyError, "未登记监听端口"):
            release_isolated_mmr_port_for_command(
                FakeContext(), ["sh", "-ec", "pg_ctl -D /tmp/unknown -w start"])

    def test_interrupted_isolated_root_is_stopped_before_removal(self):
        root = Path(tempfile.mkdtemp(prefix="fbase_regress_interrupted_"))
        child = root / "node1"
        child.mkdir()
        (child / "PG_VERSION").write_text("15\n", encoding="utf-8")

        class FakeContext(object):
            command_runner = FakeCommandRunner(
                subprocess.CompletedProcess([], 0, stdout=""))
            manager = SimpleNamespace(binary=lambda name: "/pg/bin/%s" % name)

        try:
            _remove_stale_isolated_cluster_root(FakeContext(), root)
            self.assertEqual(FakeContext.command_runner.commands, [
                ["/pg/bin/pg_ctl", "-D", str(child), "stop", "-m", "immediate"]])
            self.assertFalse(root.exists())
        finally:
            shutil.rmtree(str(root), ignore_errors=True)


class NodeResolutionTest(unittest.TestCase):
    def test_streaming_logical_and_mmr_selectors(self):
        manager = EnvironmentManager.__new__(EnvironmentManager)
        manager.cluster_name = "test"
        manager.nodes = {
            "p": {}, "s": {}, "sub": {}, "m1": {}, "m1s": {},
        }
        manager.groups = {
            "streaming": {"primary": "p", "standbys": ["s"]},
            "logical": {"publisher": "p", "subscribers": {"sub": {}}},
            "mmr": {"members": {"one": {"primary": "m1", "standbys": ["m1s"]}}},
        }
        self.assertEqual(manager.resolve_node("primary"), "p")
        self.assertEqual(manager.resolve_node("standby"), "s")
        self.assertEqual(manager.resolve_node("subscriber"), "sub")
        self.assertEqual(manager.resolve_node("mmr:one"), "m1")
        self.assertEqual(manager.resolve_node("mmr:one:standby"), "m1s")


class CommandStepTest(unittest.TestCase):
    def test_cluster_action_is_quiet_during_case_execution(self):
        calls = []
        context = SimpleNamespace(
            manager=SimpleNamespace(reload=lambda **kwargs: calls.append(kwargs)),
        )
        step = {
            "type": "cluster_action", "title": "重载配置", "action": "reload",
            "expected": "成功", "assertion": command_succeeds(),
        }
        result = StepExecutor(context).execute(step)
        self.assertEqual(result.status, "SUCCESS")
        self.assertEqual(calls, [{"quiet": True}])

    def test_node_action_operates_only_on_declared_member(self):
        calls = []
        context = SimpleNamespace(
            manager=SimpleNamespace(_pg_ctl=lambda node, action: calls.append((node, action))),
            resolve_node=lambda selector: {"mmr:two": "stream2_primary"}[selector],
        )
        step = {
            "type": "node_action", "title": "停止成员", "node": "mmr:two", "action": "stop",
            "expected": "停止成功", "assertion": command_succeeds(),
        }
        result = StepExecutor(context).execute(step)
        self.assertEqual(result.status, "SUCCESS")
        self.assertEqual(result.node, "stream2_primary")
        self.assertEqual(calls, [("stream2_primary", "stop")])

    def test_continue_on_failure_must_be_boolean(self):
        step = command_step("检查", ["true"], "返回 0", command_succeeds())
        step["continue_on_failure"] = "yes"
        with self.assertRaisesRegex(Exception, "continue_on_failure"):
            from framework.steps import validate_step
            validate_step(step)

    def test_command_step_uses_common_executor(self):
        process = subprocess.CompletedProcess([], 0, stdout="done\n")
        transport = SimpleNamespace(run=lambda *args, **kwargs: process)
        context = SimpleNamespace(
            resolve_node=lambda selector: "primary",
            transport=transport,
        )
        step = command_step(
            "执行命令", ["echo", "done"], "退出码为 0", command_succeeds())
        result = StepExecutor(context).execute(step)
        self.assertEqual(result.status, "SUCCESS")
        self.assertEqual(result.node, "primary")
        self.assertEqual(result.output, "done")

    def test_system_time_shift_requires_clock_fixture(self):
        context = SimpleNamespace(values={})
        executor = StepExecutor(context)
        step = {
            "type": "system_time_shift", "title": "推进时间", "seconds": 1,
            "expected": "成功", "assertion": command_succeeds(),
        }
        with self.assertRaisesRegex(Exception, "system_clock"):
            executor.execute(step)


class CommandRunnerTest(unittest.TestCase):
    def test_background_command_collects_output_when_finished(self):
        runner = CommandRunner(default_timeout=2)
        process = runner.start(["sh", "-c", "printf background-result"])
        completed = runner.finish(["sh", "-c", "printf background-result"], process)
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout, "background-result")

    def test_timeout_returns_124_for_nonchecking_call(self):
        with tempfile.TemporaryDirectory() as root:
            runner = CommandRunner(Path(root) / "execution.log", default_timeout=0.01)
            result = runner.run(["sh", "-c", "sleep 1"], check=False)
            self.assertEqual(result.returncode, 124)
            self.assertIn("命令执行超时", result.stdout)

    def test_timeout_terminates_shell_children(self):
        with tempfile.TemporaryDirectory() as root:
            pid_file = Path(root) / "child.pid"
            runner = CommandRunner(default_timeout=0.05)
            result = runner.run(
                ["sh", "-c", "sleep 30 & echo $! > %s; wait" % pid_file],
                check=False)
            self.assertEqual(result.returncode, 124)
            child_pid = int(pid_file.read_text().strip())
            time.sleep(0.05)
            with self.assertRaises(OSError):
                os.kill(child_pid, 0)


class RunnerReliabilityTest(unittest.TestCase):
    def test_implicit_run_groups_same_session_key_without_reordering_others(self):
        cases = [
            {"id": "first"},
            {"id": "session-a", "session": {"key": "shared"}},
            {"id": "middle"},
            {"id": "session-b", "session": {"key": "shared"}},
            {"id": "last"},
        ]

        ordered = SuiteRunner._group_session_cases(cases)

        self.assertEqual(
            [case["id"] for case in ordered],
            ["first", "session-a", "session-b", "middle", "last"])

    def test_session_order_controls_the_contiguous_group_order(self):
        cases = [
            {"id": "late", "session": {"key": "shared", "order": 20}},
            {"id": "early", "session": {"key": "shared", "order": 10}},
            {"id": "ordinary"},
        ]

        ordered = SuiteRunner._group_session_cases(cases)

        self.assertEqual(
            [case["id"] for case in ordered], ["early", "late", "ordinary"])

    def test_session_boundary_cleans_only_inactive_sessions(self):
        cleaned = []

        class FakeContext(object):
            def __init__(self, key):
                self.key = key

            def cleanup(self):
                cleaned.append(self.key)
                return []

        sessions = {
            "shared": {"context": FakeContext("shared"), "error": ""},
            "other": {"context": FakeContext("other"), "error": ""},
        }
        errors = SuiteRunner.__new__(SuiteRunner)._cleanup_inactive_sessions(
            sessions, "shared")

        self.assertEqual(errors, [])
        self.assertEqual(cleaned, ["other"])
        self.assertEqual(list(sessions), ["shared"])

    def test_server_log_collection_error_fails_the_case(self):
        status, error = SuiteRunner._with_server_log_errors(
            "SUCCESS", "", ["stderr: permission denied"])
        self.assertEqual(status, "FAILED")
        self.assertIn("服务端日志收集失败", error)


if __name__ == "__main__":
    unittest.main()
