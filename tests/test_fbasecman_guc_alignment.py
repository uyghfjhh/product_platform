"""Contract tests for GUC evidence, protocol boundaries and honest coverage."""
import json
from pathlib import Path
from typing import ClassVar

import pytest
from platform_regress.clients import pgwire as wire
from platform_regress.sdk import CaseContext, RegressionEngine

from products.fbasecman import guc_alignment_native as alignment


def frame(kind, body=b""):
    return wire.message(kind, body)


class Socket:
    def __init__(self, responses):
        self.responses = bytearray(responses)
        self.sent = []

    def recv(self, n):
        chunk = bytes(self.responses[:n])
        del self.responses[:n]
        return chunk

    def sendall(self, packet):
        self.sent.append(packet)

    def close(self):
        pass


def probe_with_socket(tmp_path, responses):
    context = CaseContext("guc.extended_boundary_hint", tmp_path)
    plan = alignment.CheckPlan("mmr", "transaction", True, "E", "extended_parse_no_execute")
    runner = alignment.ScenarioRunner(context, plan, "hint", 5432)
    probe = object.__new__(alignment.Probe)
    probe.runner, probe.label, probe.sequence, probe.parameters = runner, "A", 0, {}
    probe.sock = Socket(responses)
    return probe, context


def events(context):
    return [json.loads(line) for line in (context.output_dir / "events.jsonl").read_text().splitlines()]


@pytest.mark.parametrize("name,value,expected", [
    ("work_mem", "8MB", 8388608), ("work_mem", "8192kB", 8388608),
    ("statement_timeout", "7s", 7000), ("statement_timeout", "7000ms", 7000),
    ("statement_timeout", "0", 0), ("TimeZone", "UTC", "UTC"),
])
def test_parameter_values_are_exact_and_normalized(name, value, expected):
    assert alignment.normalize(name, value) == expected


def test_incompatible_unit_does_not_silently_match():
    with pytest.raises(ValueError):
        alignment.normalize("work_mem", "8s")


def test_parameter_status_rejects_truncated_or_extra_fields():
    assert wire.parameter_status(b"TimeZone\0UTC\0") == ("TimeZone", "UTC")
    for bad in (b"TimeZone\0UTC", b"\0UTC\0", b"TimeZone\0UTC\0extra\0"):
        with pytest.raises(RuntimeError):
            wire.parameter_status(bad)


def test_flush_phase_does_not_wait_for_ready_or_consume_next_phase(tmp_path):
    probe, _ = probe_with_socket(tmp_path, frame("1") + frame("2"))
    actual = probe.exchange(wire.flush_message(), "Parse/Flush", {"received": ["1"], "ready": []}, terminal={"1"})
    assert actual["received"] == ["1"]
    assert probe.sock.responses == frame("2")


def test_error_ends_flush_without_waiting_for_ignored_success():
    sock = Socket(frame("E", b"C22012\0Mdivision by zero\0\0") + frame("Z", b"I"))
    messages = wire.read_messages_until(sock, {"C"})
    assert [k for k, _ in messages] == ["E"]
    assert sock.responses == frame("Z", b"I")


def test_response_preserves_all_command_tags_and_report_values(tmp_path):
    responses = (frame("C", b"BEGIN\0") + frame("S", b"TimeZone\0Asia/Shanghai\0") +
                 frame("C", b"SET\0") + frame("C", b"COMMIT\0") + frame("Z", b"I"))
    probe, _ = probe_with_socket(tmp_path, responses)
    result = probe.exchange(wire.simple_query_message("BEGIN; SET TimeZone='Asia/Shanghai'; COMMIT;"), "单 Q", {
        "tags": ["BEGIN", "SET", "COMMIT"], "ready": ["I"], "sqlstates": []})
    assert result["parameters"] == [("TimeZone", "Asia/Shanghai")]
    assert probe.parameters["TimeZone"] == "Asia/Shanghai"


def test_mismatch_is_recorded_with_actual_and_wire_before_raise(tmp_path):
    probe, context = probe_with_socket(tmp_path, frame("C", b"SET\0") + frame("Z", b"I"))
    with pytest.raises(AssertionError, match="tags"):
        probe.exchange(b"packet", "P/Sync 未 E", {"tags": [], "ready": ["I"]})
    event = next(e for e in events(context) if e.get("kind") == "step.finished")
    assert event["payload"]["status"] == "FAIL"
    details = event["payload"]["details"]
    assert details["actual"]["tags"] == ["SET"]
    assert details["expected"]["tags"] == []
    assert details["assertion"]["differences"]["tags"]["actual"] == ["SET"]
    assert details["evidence"]


def test_partial_wire_is_preserved_on_disconnect(tmp_path):
    probe, context = probe_with_socket(tmp_path, frame("1"))
    with pytest.raises(AssertionError, match="connection closed"):
        probe.exchange(b"packet", "P/E/Sync", {"ready": ["I"]})
    output = list((context.output_dir / "artifacts").rglob("*wire.json"))
    assert output
    assert json.loads(output[0].read_text())["received"] == [{"kind": "1", "payload_hex": ""}]


def test_all_design_items_and_acceptance_have_registered_scenarios():
    names = {s for values in alignment.SCENARIOS.values() for s in values}
    assert len(names) == 24
    assert set(alignment.DESIGN_ITEMS) == set(range(1, 17))
    assert len(alignment.ACCEPTANCE) == 13
    assert all(set(v).issubset(names) for v in alignment.DESIGN_ITEMS.values())
    assert all(set(v).issubset(names) for v in alignment.ACCEPTANCE.values())


def test_q_configuration_matrix_and_internal_boundary_are_mandatory():
    plan = alignment.make_plan("transaction_sync")
    assert {(p.pool, p.reserve) for p in plan if p.protocol == "Q" and p.scenario == "tx_set_commit"} == {
        ("transaction", True), ("transaction", False), ("session", True), ("session", False)}
    assert {p.topology for p in plan} == {"mmr", "replication"}
    assert {p.topology for p in plan if p.scenario == "product_cache_boundaries"} == {"mmr", "replication"}
    assert all(p.reserve for p in plan if p.protocol == "E")


def test_eight_native_targets_are_registered_without_removing_existing_guc():
    from products.fbasecman.cases import CASE_METADATA, CASES
    targets = {f"guc.{group}_{mode}" for group in alignment.SCENARIOS for mode in ("hint", "sql_parse")}
    assert targets.issubset(CASES)
    assert len(targets) == 8
    assert all(isinstance(CASES[target], alignment.GucAlignmentCase) for target in targets)
    assert sum(c["suite"] == "guc" and c["target"] not in targets for c in CASE_METADATA) == 18


def test_missing_binary_preserves_entire_planned_denominator(tmp_path):
    case = alignment.GucAlignmentCase("extended_boundary", "hint")
    context = CaseContext("guc.extended_boundary_hint", tmp_path)
    result = RegressionEngine().run(case, context)
    assert result.verdict == "BLOCKED"
    assert len(case.coverage) == len(alignment.make_plan("extended_boundary"))
    assert all(not row["executed"] and row["status"] == "BLOCKED" for row in case.coverage)


def test_failure_survives_later_pass_and_independent_scenarios_continue(tmp_path, monkeypatch):
    case = alignment.GucAlignmentCase("savepoint_report", "hint")
    context = CaseContext("guc.savepoint_report_hint", tmp_path, environment={"fbasecman_bin": "/bin/true"})
    monkeypatch.setattr(alignment, "render_alignment_config", lambda *a: 12345)
    monkeypatch.setattr(alignment, "wait_routing", lambda *a: None)
    monkeypatch.setattr(context, "attach_file", lambda *a: "config")
    monkeypatch.setattr(context, "start_process", lambda *a, **kw: object())
    def run(runner):
        if runner.plan.scenario == "savepoint_rollback":
            raise AssertionError("实际32MB，期望16MB")
    monkeypatch.setattr(alignment.ScenarioRunner, "run", run)
    result = RegressionEngine().run(case, context)
    assert result.verdict == "FAIL"
    assert any(row["status"] == "PASS" for row in case.coverage)
    assert any(row["status"] == "FAIL" for row in case.coverage)
    assert any(row["scenario"] == "product_cache_boundaries" and row["status"] == "BLOCKED" for row in case.coverage)


def test_replication_configuration_uses_only_selected_mode_and_existing_user(tmp_path):
    class Context:
        environment: ClassVar[dict] = {"guc_alignment_mode_users_ready": True, "fbasecman_bin": "/bin/true", "license_dir": "/tmp", "proxy_port": 12345,
                       "nodes": {"mmr1": {"host": "127.0.0.1", "port": 1}, "mmr2": {"host": "127.0.0.1", "port": 2}},
                       "extra_nodes": {"pg_3": {"host": "127.0.0.1", "port": 3, "application_name": "pg_3"}}}
        def sql(self, node, sql):
            from platform_regress.sdk import SqlResult
            value = "dev_group" if "SELECT group_name" in sql else "uuid" if "group_uuid" in sql else "123"
            return SqlResult(((value,),), ("v",), "SELECT 1")
    plan = alignment.CheckPlan("replication", "session", False, "Q", "tx_set_commit")
    path = tmp_path / "fbasecman.conf"
    alignment.render_alignment_config(Context(), path, plan, "hint")
    text = path.read_text()
    assert 'real_group_name "dev_group"' in text
    assert text.count('group_names "rep_group"') == 1
    assert text.count('pool "session"') == 2  # postgres business rule + admin
    assert text.count('pool_reserve_prepared_statement no') == 1
    assert 'rw_split_method "hint"' in text


def test_cleanup_and_internal_gate_cannot_turn_partial_sql_pass_into_full_pass(tmp_path, monkeypatch):
    case = alignment.GucAlignmentCase("savepoint_report", "hint")
    context = CaseContext("guc.savepoint_report_hint", tmp_path, environment={"fbasecman_bin": "/bin/true"})
    monkeypatch.setattr(alignment, "render_alignment_config", lambda *a: 12345)
    monkeypatch.setattr(alignment, "wait_routing", lambda *a: None)
    monkeypatch.setattr(context, "attach_file", lambda *a: "config")
    monkeypatch.setattr(context, "start_process", lambda *a, **kw: object())
    monkeypatch.setattr(alignment.ScenarioRunner, "run", lambda *a: None)
    result = RegressionEngine().run(case, context)
    assert result.verdict == "BLOCKED"
    output = next((tmp_path / "artifacts").rglob("guc-alignment-coverage.json"))
    facts = json.loads(output.read_text())
    assert facts["passed"] > 0
    assert facts["unexecuted"] == 2
    assert facts["internal_acceptance"]["status"] == "BLOCKED"
    assert set(facts["design_coverage"]) == {str(i) for i in range(1, 17)}
    assert len(facts["acceptance_coverage"]) == 13


def test_selection_retains_unexecuted_items_and_full_denominator(tmp_path, monkeypatch):
    case = alignment.GucAlignmentCase("savepoint_report", "hint")
    context = CaseContext("guc.savepoint_report_hint", tmp_path, environment={
        "fbasecman_bin": "/bin/true", "guc_alignment_scenarios": ["savepoint_rollback"],
        "guc_alignment_topologies": ["mmr"]})
    monkeypatch.setattr(alignment, "render_alignment_config", lambda *a: 12345)
    monkeypatch.setattr(alignment, "wait_routing", lambda *a: None)
    monkeypatch.setattr(context, "attach_file", lambda *a: "config")
    monkeypatch.setattr(context, "start_process", lambda *a, **kw: object())
    monkeypatch.setattr(alignment.ScenarioRunner, "run", lambda *a: None)
    result = RegressionEngine().run(case, context)
    assert result.verdict == "BLOCKED"
    assert len(case.coverage) == len(alignment.make_plan("savepoint_report"))
    assert all(not row["executed"] for row in case.coverage if row["topology"] == "replication")


def test_existing_session_reserve_restriction_is_recorded_without_changing_switch(tmp_path, monkeypatch):
    case = alignment.GucAlignmentCase("savepoint_report", "hint")
    context = CaseContext("guc.savepoint_report_hint", tmp_path, environment={"fbasecman_bin": "/bin/true"})
    monkeypatch.setattr(alignment, "render_alignment_config", lambda *a: 12345)
    monkeypatch.setattr(alignment, "wait_routing", lambda *a: None)
    monkeypatch.setattr(context, "attach_file", lambda *a: "config")
    def start(*a, **kw):
        (tmp_path / "process-1.log").write_text("prepared statements support in session pool makes no sence")
        raise RuntimeError("产品进程提前退出")
    monkeypatch.setattr(context, "start_process", start)
    result = RegressionEngine().run(case, context)
    assert result.verdict == "ERROR"  # transaction config is an unrelated infrastructure error
    rows = [row for row in case.coverage if row["pool"] == "session" and row["reserve"] and row["protocol"] != "product"]
    assert rows and all(row["status"] == "SKIPPED" for row in rows)
    assert all(row["reserve"] for row in rows)


def test_compatibility_plan_includes_disabled_sync_and_extended_passthrough():
    plans = [p for p in alignment.make_plan('extended_boundary') if p.scenario == 'compatibility_scope']
    assert len(plans) == 10
    assert {(p.protocol, p.reserve) for p in plans if not p.enable_sync} == {
        ('Q', True), ('Q', False), ('E', True), ('E', False)}
    assert all(not p.reserve and p.protocol == 'E' for p in plans if p.enable_sync)


def test_complete_run_can_pass_without_permanent_internal_blocked(tmp_path, monkeypatch):
    from products.fbasecman import guc_instrumentation
    case = alignment.GucAlignmentCase('savepoint_report', 'hint')
    context = CaseContext('guc.savepoint_report_hint', tmp_path, environment={'fbasecman_bin': '/bin/true'})
    monkeypatch.setattr(alignment, 'render_alignment_config', lambda *a: 12345)
    monkeypatch.setattr(alignment, 'wait_routing', lambda *a: None)
    monkeypatch.setattr(context, 'attach_file', lambda *a: 'config')
    monkeypatch.setattr(context, 'start_process', lambda *a, **kw: object())
    monkeypatch.setattr(guc_instrumentation, 'build_test_proxy', lambda *a: Path('/bin/true'))
    monkeypatch.setattr(alignment.ScenarioRunner, 'run', lambda *a: None)
    result = RegressionEngine().run(case, context)
    assert result.verdict == 'PASS'
    report = next((tmp_path / 'artifacts').rglob('guc-alignment-coverage.json'))
    assert json.loads(report.read_text())['internal_acceptance']['status'] == 'PASS'


def test_product_fault_driver_executes_all_points_after_first_failure(tmp_path):
    from products.fbasecman.guc_instrumentation import WRAPPED
    assert {'fb_guc_cache_insert_or_update', 'fb_add_outstanding_request', 'mm_socket_writev',
            'fb_sql_parse_client_state_prepare_forbidden', 'machine_iov_add', 'od_reset'}.issubset(WRAPPED)


def test_compatibility_config_does_not_silently_enable_sync(tmp_path):
    class Context:
        environment: ClassVar[dict] = {'fbasecman_bin': '/bin/true', 'license_dir': '/tmp', 'proxy_port': 12345,
            'nodes': {'mmr1': {'host': '127.0.0.1', 'port': 1}, 'mmr2': {'host': '127.0.0.1', 'port': 2}}}
        def sql(self, node, sql):
            from platform_regress.sdk import SqlResult
            return SqlResult((('g1' if 'SELECT group_name' in sql else 'uuid',),), ('v',), 'SELECT 1')
    plan = alignment.CheckPlan('mmr', 'transaction', False, 'E', 'compatibility_scope', False)
    output = tmp_path / 'cman.conf'
    alignment.render_alignment_config(Context(), output, plan, 'hint')
    assert 'enable_guc_sync no' in output.read_text()
    assert 'pool_reserve_prepared_statement no' in output.read_text()


def test_reset_defaults_do_not_use_sdk_startup_timeout(tmp_path, monkeypatch):
    import psycopg
    calls = []
    class Connection:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def execute(self, sql):
            assert "current_setting('statement_timeout')" in sql
            return self
        def fetchall(self): return [('4MB', '0', 'UTC')]
    def connect(**kwargs):
        calls.append(kwargs)
        return Connection()
    monkeypatch.setattr(psycopg, 'connect', connect)
    context = CaseContext('guc.defaults', tmp_path, environment={
        'nodes': {'mmr1': {'host': 'localhost', 'port': 5432}}, 'user': 'postgres'})
    actual = alignment.clean_backend_defaults(context, 'mmr1')
    assert actual == {'work_mem': '4MB', 'statement_timeout': '0', 'TimeZone': 'UTC'}
    assert calls[0]['options'] == ''
    assert calls[0]['connect_timeout'] == 10


def test_frozen_baseline_detects_changed_sql_parse_result(tmp_path):
    context = CaseContext('guc.extended_boundary_hint', tmp_path / 'out', environment={
        'fbasecman_bin': '/bin/true', 'guc_alignment_baseline_dir': str(tmp_path / 'baselines')})
    runner = alignment.ScenarioRunner(context, alignment.CheckPlan('mmr', 'transaction', True, 'E', 'extended_execute_apply'), 'hint', 1)
    initial = {'received': ['C', 'Z'], 'sqlstates': [], 'tags': ['SET'], 'ready': ['I']}
    measured = {'rows': [['32MB']]}
    alignment.freeze_mixed_baseline(runner, True, False, initial, measured, True)
    alignment.freeze_mixed_baseline(runner, True, False, initial, measured, True)
    with pytest.raises(AssertionError, match='基线'):
        alignment.freeze_mixed_baseline(runner, True, False, initial, {'rows': [['64MB']]}, True)


def test_test_proxy_requires_build_artifacts_not_product_business_hooks(tmp_path):
    from platform_regress.sdk import Blocked

    from products.fbasecman.guc_instrumentation import build_test_proxy
    context = CaseContext('guc.internal', tmp_path, environment={'fbasecman_bin': '/bin/true'})
    with pytest.raises(Blocked, match='对象文件'):
        build_test_proxy(context)


def test_session_pool_baseline_uses_real_write_transaction(tmp_path):
    calls = []
    class Probe:
        def snapshot(self, expected=None, **kwargs):
            calls.append(('snapshot', expected))
            return {'work_mem': '4MB', 'statement_timeout': '0', 'TimeZone': 'UTC'}
        def sql(self, sql, **kwargs):
            calls.append((sql, kwargs))
    context = CaseContext('guc.baseline', tmp_path)
    runner = alignment.ScenarioRunner(context, alignment.CheckPlan('mmr', 'session', False, 'Q', 'tx_set_commit'), 'sql_parse', 1)
    runner.baseline(Probe())
    assert calls[1][0] == 'BEGIN READ WRITE'
    assert all(kwargs['ready'] == 'T' for sql, kwargs in calls[2:5])
    assert calls[5][0] == 'COMMIT'
    assert calls[6][0] == 'snapshot'


@pytest.mark.parametrize('protocol', ['Q', 'E'])
def test_hint_write_route_label_is_before_begin_and_never_inside_transaction(tmp_path, protocol):
    calls = []
    class Probe:
        def sql(self, sql, **kwargs):
            calls.append((sql, kwargs))
    context = CaseContext('guc.hint_report', tmp_path)
    runner = alignment.ScenarioRunner(context, alignment.CheckPlan('mmr', 'transaction', True, protocol, 'report_parameter_status'), 'hint', 1)
    runner.begin_write_transaction(Probe(), protocol=protocol)
    assert calls == [
        ('SET SESSION CHARACTERISTICS AS TRANSACTION READ WRITE', {'protocol': 'Q'}),
        ('BEGIN', {'tag': 'BEGIN', 'ready': 'T', 'protocol': protocol})]


def test_sql_parse_write_begin_does_not_use_hint_label(tmp_path):
    calls = []
    class Probe:
        def sql(self, sql, **kwargs):
            calls.append((sql, kwargs))
    context = CaseContext('guc.sql_parse_report', tmp_path)
    runner = alignment.ScenarioRunner(context, alignment.CheckPlan('mmr', 'transaction', True, 'E', 'report_parameter_status'), 'sql_parse', 1)
    runner.begin_write_transaction(Probe())
    assert calls == [('BEGIN READ WRITE', {'tag': 'BEGIN', 'ready': 'T', 'protocol': None})]


@pytest.mark.parametrize('protocol,reserve,scenario,excluded', [
    ('Q', False, 'routing_and_discard_boundaries', True),
    ('Q', True, 'routing_and_discard_boundaries', False),
    ('E', False, 'routing_and_discard_boundaries', False),
    ('Q', False, 'tx_set_commit', False),
    ('Q', False, 'report_parameter_status', False),
])
def test_existing_discard_queue_issue_exclusion_is_precise(protocol, reserve, scenario, excluded):
    plan = alignment.CheckPlan('mmr', 'transaction', reserve, protocol, scenario)
    assert alignment.known_discard_q_registration_limit(plan) is excluded


def test_report_repeated_session_pool_branches_reset_physical_timezone(tmp_path):
    calls = []
    class Probe:
        def sql(self, sql, **kwargs):
            calls.append((sql, kwargs))
    context = CaseContext('guc.report-prepare', tmp_path)
    runner = alignment.ScenarioRunner(context, alignment.CheckPlan('mmr', 'session', False, 'Q', 'report_parameter_status'), 'sql_parse', 1)
    runner.prepare_report_timezone(Probe())
    assert calls == [
        ('BEGIN READ WRITE', {'tag': 'BEGIN', 'ready': 'T', 'protocol': None}),
        ("SET TimeZone='UTC'", {'tag': 'SET', 'ready': 'T'}),
        ('COMMIT', {'tag': 'COMMIT'})]


def test_user_selected_single_account_excludes_cross_mode_account_test(tmp_path):
    context = CaseContext('guc.owner', tmp_path)
    plan = alignment.CheckPlan('mmr', 'transaction', True, 'E', 'mode_owner_isolation')
    runner = alignment.ScenarioRunner(context, plan, 'sql_parse', 5432)
    with pytest.raises(alignment.BaselineLimit, match='现有 postgres'):
        runner.mode_isolation()


def test_sql_parse_baseline_reuses_existing_account_and_proxy(tmp_path):
    context = CaseContext('guc.baseline', tmp_path)
    plan = alignment.CheckPlan('mmr', 'transaction', True, 'E', 'extended_execute_apply')
    runner = alignment.ScenarioRunner(context, plan, 'sql_parse', 5432)
    with runner.sql_parse_baseline_runner() as baseline:
        assert baseline is runner
    assert 'guc_alignment_hint_user' not in context.environment
    assert 'guc_alignment_sql_parse_user' not in context.environment


def test_parameter_observation_is_one_business_step_with_precise_values(tmp_path):
    probe, context = probe_with_socket(tmp_path, b'')
    probe.last_operation = 'Q：COMMIT'
    probe.sql = lambda *a, **kw: {'rows': [['32MB','7s','UTC','hint_base','host','5432','123','false']]}
    probe.snapshot({'work_mem':'32MB','statement_timeout':'7s'})
    checks = [e['payload'] for e in events(context) if e['kind']=='step.finished']
    assert len(checks) == 1
    assert '提交事务后' in checks[0]['title']
    assert checks[0]['details']['expected'] == {'work_mem':'32MB','statement_timeout':'7s'}
    assert '期望 32MB，实际 32MB' in checks[0]['details']['analysis']
