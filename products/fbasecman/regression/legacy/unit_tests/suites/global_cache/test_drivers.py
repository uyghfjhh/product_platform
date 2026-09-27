import unittest
from pathlib import Path

from suites.global_cache.drivers import (
    DriverError,
    PHASED_JDBC_ACTIONS,
    driver_api_calls,
    libpq_prepared_operations,
    normalize_phased_prepared_operations,
    normalize_prepared_sequence,
)


class PreparedSequenceTest(unittest.TestCase):
    def test_normalizes_supported_operations(self):
        operations = normalize_prepared_sequence([
            ("set_guc", "statement", "SET application_name = 'demo'"),
            ("query", "query_int:1", "SELECT ?"),
            ("multi", "query_columns:first,second", "SELECT 1, 2"),
            ("execute", "execute", "RESET ALL"),
        ])

        self.assertEqual("set_guc", operations[0]["output_key"])
        self.assertEqual("query_int:1", operations[1]["mode"])
        self.assertEqual("query_columns:first,second", operations[2]["mode"])
        self.assertEqual("RESET ALL", operations[3]["sql"])

    def test_rejects_unknown_operation_mode(self):
        with self.assertRaisesRegex(DriverError, "unsupported prepared sequence mode"):
            normalize_prepared_sequence([("bad", "parse", "SELECT 1")])

    def test_rejects_incomplete_operation(self):
        with self.assertRaisesRegex(DriverError, "must have output key"):
            normalize_prepared_sequence([("missing", "query")])

    def test_rejects_query_columns_without_keys(self):
        with self.assertRaisesRegex(DriverError, "requires at least one output key"):
            normalize_prepared_sequence([("bad", "query_columns:", "SELECT 1")])


class PhasedPreparedTest(unittest.TestCase):
    def test_normalizes_multiple_active_operations(self):
        operations = normalize_phased_prepared_operations([
            ("active_01", "SELECT ?", 1),
            ("active_02", "SELECT ?", "2"),
        ])

        self.assertEqual([1, 2], [item["bind_value"] for item in operations])

    def test_rejects_empty_or_non_integer_operations(self):
        with self.assertRaises(DriverError):
            normalize_phased_prepared_operations([])
        with self.assertRaises(DriverError):
            normalize_phased_prepared_operations([("active", "SELECT ?", "bad")])


class DriverEvidenceTest(unittest.TestCase):
    def test_extracts_calls_from_real_java_and_libpq_assets(self):
        root = Path(__file__).resolve().parents[3] / "suites" / "global_cache" / "assets"

        java_calls = driver_api_calls(root / "jdbc" / "GC_basic_reuse.java")
        libpq_calls = driver_api_calls(
            root / "libpq" / "GC_prepare_before_bind_deploy.c"
        )

        self.assertIn("conn.prepareStatement()", java_calls)
        self.assertTrue(any(call.startswith("PQprepare") for call in libpq_calls))
        self.assertTrue(any(call.startswith("PQexec") or call.startswith("PQsend") for call in libpq_calls))

    def test_jdbc_assets_have_live_observation_markers(self):
        root = Path(__file__).resolve().parents[3] / "suites" / "global_cache" / "assets" / "jdbc"
        expected = {
            "basic_reuse.java": "PHASE=AFTER_FIRST",
            "cross_client_reuse.java": "PHASE=AFTER_CLIENT1",
            "prepared_single_query.java": "PHASE=AFTER_EXECUTE",
            "heartbeat_reload_reclassifies_existing_normal_entry.java": "PHASE=AFTER_EXECUTE",
            "same_sql_different_users.java": "PHASE=AFTER_USER1",
            "parse_invalid_error_recovery_same_connection.java": "PHASE=AFTER_FIRST_FAILURE",
            "prepared_sql_sequence.java": "PHASE=AFTER_",
        }
        for name, marker in expected.items():
            source = (root / ("GC_" + name)).read_text(encoding="utf-8")
            self.assertIn(marker, source)
        self.assertEqual(
            {
                "basic_reuse",
                "cross_client_reuse",
                "heartbeat_reload_reclassifies_existing_normal_entry",
                "parse_invalid_error_recovery_same_connection",
            },
            set(PHASED_JDBC_ACTIONS),
        )

    def test_extracts_phase_sql_and_parameters_from_real_libpq_asset(self):
        source = (
            Path(__file__).resolve().parents[3]
            / "suites" / "global_cache" / "assets" / "libpq"
            / "GC_backend_global_split_eviction.c"
        )

        seed = libpq_prepared_operations(source, phase="seed")
        trigger = libpq_prepared_operations(source, phase="trigger")

        self.assertEqual(5, len(seed))
        self.assertEqual("SELECT $1::int /* gc_split_initial_1 */", seed[0]["sql"])
        self.assertEqual(["$1=1"], seed[0]["parameters"])
        self.assertEqual(1, len(trigger))
        self.assertEqual("SELECT $1::int /* gc_split_pressure_6 */", trigger[0]["sql"])
        self.assertEqual(["$1=6"], trigger[0]["parameters"])
