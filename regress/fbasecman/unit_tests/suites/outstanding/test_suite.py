import unittest
from pathlib import Path

from suites.outstanding.suite import (
    _parse_console_cache,
    _parse_pg_cache,
    _post_disconnect_ref_state,
)


def _get_source_root() -> Path:
    direct = Path(__file__).resolve().parents[4] / "sources"
    if direct.is_dir():
        return direct
    fbasecman = Path(__file__).resolve().parents[4] / "fbasecman_dev" / "sources"
    if fbasecman.is_dir():
        return fbasecman
    return direct


class OutstandingSuiteParserTest(unittest.TestCase):
    def test_relay_buffers_close_and_execute_when_local_parsing_is_enabled(self):
        source_root = _get_source_root()
        relay = (source_root / "relay.h").read_text(encoding="utf-8")
        frontend = (source_root / "frontend.c").read_text(encoding="utf-8")
        switch_block = relay.split(
            "od_relay_full_packet_required_client_to_server", 1)[1].split(
                "od_relay_full_packet_required_server_to_client", 1)[0]
        self.assertIn("case KIWI_FE_EXECUTE:", switch_block)
        self.assertIn("case KIWI_FE_CLOSE:", switch_block)
        self.assertGreaterEqual(
            frontend.count(
                "pool->reserve_prepared_statement ||\n\t    "
                "initial_route->rule->log_query"),
            1,
        )
        self.assertIn(
            "route->rule->pool->reserve_prepared_statement ||\n\t    "
            "route->rule->log_query",
            frontend,
        )

    def test_execute_parser_consumes_max_rows_and_rejects_trailing_data(self):
        source_root = _get_source_root()
        kiwi = (source_root.parent / "third_party" / "kiwi" / "kiwi" /
                "be_read.h").read_text(encoding="utf-8")
        parser = (source_root / "parser" / "fb_frontend.c").read_text(
            encoding="utf-8")
        execute_reader = kiwi.split("kiwi_be_read_execute", 1)[1].split(
            "kiwi_be_read_close", 1)[0]
        self.assertIn("kiwi_read32(&max_rows", execute_reader)
        self.assertIn("pos_size != 0", execute_reader)
        self.assertIn("KIWI_PROTOCOL_VIOLATION", parser)
        self.assertIn("fb_sql_parse_reject_invalid_execute", parser)

    def test_parse_pg_cache_uses_latest_phase(self):
        output = """PG_CACHE_BEGIN
PG_CACHE|__fbasecman_1|SELECT 1 /* outstanding_case_demo_old */
PG_CACHE_END
PG_CACHE_BEGIN
PG_CACHE|__fbasecman_2|SELECT 2 /* outstanding_case_demo_new */
PG_CACHE_END
"""
        self.assertEqual(
            {"__fbasecman_2": "select 2 /* outstanding_case_demo_new */"},
            _parse_pg_cache(output),
        )

    def test_parse_console_cache_filters_unrelated_rows(self):
        output = """type\tuser\tdatabase\tnode_name\tserver_state\tsid\tglobal_name\tbackend_ps_name\tdefinition\trefcount
S\tpostgres\tpostgres\tpg_1\tidle\ts1\t__fbasecman_1\t__fbasecman_1\tSELECT 1 /* unrelated */\t1
S\tpostgres\tpostgres\tpg_1\tidle\ts1\t__fbasecman_2\t__fbasecman_2\tSELECT 2 /* outstanding_case_demo */\t1
"""
        self.assertEqual(
            {"__fbasecman_2": "select 2 /* outstanding_case_demo */"},
            _parse_console_cache(output, "outstanding_case_demo"),
        )

    def test_post_disconnect_refs_match_server_owners(self):
        server = """global_name\tdefinition
__fbasecman_2\tSELECT 2 /* outstanding_case_demo */
"""
        global_cache = """global_name\tdescription\tsql_class\thas_bypass_response\tref_count
__fbasecman_1\tSELECT 1 /* outstanding_case_demo_failed */\tNORMAL\t0\t0
__fbasecman_2\tSELECT 2 /* outstanding_case_demo */\tNORMAL\t0\t1
"""
        consistent, server_counts, global_refs = _post_disconnect_ref_state(
            server, global_cache, "outstanding_case_demo")
        self.assertTrue(consistent)
        self.assertEqual({"__fbasecman_2": 1}, server_counts)
        self.assertEqual({"__fbasecman_1": 0, "__fbasecman_2": 1}, global_refs)
