import importlib.util
import json
from pathlib import Path

from platform_regress.reporting.case_report import _event_steps, _steps_payload


def test_cman_parser_recovers_same_execution_assertions(tmp_path):
    path = Path(__file__).parents[1] / 'products/fbasecman/reports/parser.py'
    spec = importlib.util.spec_from_file_location('cman_report_parser', path)
    parser = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(parser)
    (tmp_path / 'report.txt').write_text('结论: PASS\n步骤 1: 执行 SQL\n实际输出: 1 行：uuid\n判定: PASS\n')
    (tmp_path / 'events.jsonl').write_text(json.dumps({
        'kind': 'step.finished', 'payload': {'title': '核对 JDBC 协议与事务恢复',
        'status': 'PASS', 'details': {'intent': 'verify', 'required': ['PARAM_VALUE=42'],
        'output': 'PARAM_VALUE=42', 'analysis': '全部声明标记符合预期'}}}) + '\n')
    report = parser.parse_report('ha_commands.sql_parse_extended_protocol', tmp_path)
    fact = next(s for s in report['steps'] if s['title'] == '核对 JDBC 协议与事务恢复')
    assert '程序输出必须同时包含' in fact['expected']
    assert 'PARAM_VALUE=42' in fact['expected']
    assert fact['actual'] == '参数查询返回值：42'
    assert fact['analysis'] == '全部声明标记符合预期'
    assert fact['intent'] == 'verify'


def test_transport_records_are_actions_not_business_assertions(tmp_path):
    evidence = tmp_path / 'sql.json'
    evidence.write_text(json.dumps({'sql': 'SELECT 1', 'rows': [[1]], 'database': 'postgres'}))
    (tmp_path / 'events.jsonl').write_text(json.dumps({
        'kind': 'sql.finished', 'payload': {'node': 'mmr1', 'evidence': 'sql.json'}}) + '\n')
    fact = _steps_payload('test', _event_steps(tmp_path))['steps'][0]
    assert fact['intent'] == 'action'
    assert '不包含业务结果断言' in fact['expected']
    assert fact['actual'] == '1 行：1'
    assert '独立断言' in fact['analysis']


def test_configuration_evidence_extensions_are_artifacts():
    from platform_regress.reporting.renderer import _is_artifact_reference
    assert _is_artifact_reference('artifacts/run/jdbc-config-diff.diff')
    assert _is_artifact_reference('artifacts/run/jdbc-snapshot.conf')


def test_ha_journal_preserves_business_analysis_in_report_steps(tmp_path):
    from platform_regress.runtime import ReportRuntime, ReportSpec
    runtime = ReportRuntime(
        tmp_path, ReportSpec('case', 'ha_commands.case', 'HA command', 'ha_commands'),
        case_dir=tmp_path / 'case', lock_dir=tmp_path / 'locks', context_data={})
    runtime.step_journal.append({
        'order': 1, 'title': 'SET NODE WRITE 生效检查', 'execution': [],
        'intermediate': [], 'evidence': [], 'expected': 'SHOW 状态为 WRITE_CLUSTER',
        'actual': 'write_source=WRITE_CLUSTER', 'result': 'PASS',
        'intent': 'verify', 'analysis': '配置已持久化且 SHOW 状态已生效',
        'assertion': {'type': 'field_equals', 'field': 'write_source', 'value': 'WRITE_CLUSTER'},
    })
    steps = runtime._timeline_steps()
    assert steps[0].intent == 'verify'
    assert ('结果分析', '配置已持久化且 SHOW 状态已生效') in steps[0].details
    assert steps[0].assertion['field'] == 'write_source'


def test_missing_purpose_uses_labeled_current_catalog(tmp_path):
    path = Path(__file__).parents[1] / 'products/fbasecman/reports/parser.py'
    spec = importlib.util.spec_from_file_location('cman_report_purpose', path)
    parser = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(parser)
    (tmp_path / 'report.txt').write_text('结论: PASS\n验证目的:\n  ha_commands.sql_parse_extended_protocol\n')
    report = parser.parse_report('ha_commands.sql_parse_extended_protocol', tmp_path)
    assert 'SELECT ?::int' in report['purpose']
    assert report['purpose_source'] == 'current_catalog'


def test_jdbc_results_compare_values_and_preserve_code_examples():
    from types import SimpleNamespace
    from products.fbasecman.native import SqlParseExtendedProtocolCase
    class Context:
        def __init__(self):
            self.records = []
        def step(self, key, title, **record):
            self.records.append((key, record))
    stdout = '\n'.join(['PARAM_VALUE=42', 'ROLLBACK_ERROR=22012', 'ROLLBACK_VALUE=42',
                        'ROLLBACK_RECOVERY=OK', 'COMMIT_ERROR=22012', 'COMMIT_VALUE=42',
                        'COMMIT_RECOVERY=OK'])
    context = Context()
    SqlParseExtendedProtocolCase.record_results(context, SimpleNamespace(stdout=stdout, returncode=0))
    assert all(record['status'] == 'PASS' for _, record in context.records)
    parameter = context.records[0][1]['details']
    assert 'statement.setInt(1, 42)' in parameter['example_code']
    assert parameter['actual'] == '参数查询返回值：42'
    assert 'example_code' not in context.records[-1][1]['details']
    import pytest
    for bad in [stdout.replace('PARAM_VALUE=42', 'PARAM_VALUE=420'),
                stdout.replace('COMMIT_VALUE=42', 'COMMIT_VALUE=7'),
                stdout.replace('ROLLBACK_ERROR=22012', 'ROLLBACK_ERROR=08006')]:
        context = Context()
        with pytest.raises(AssertionError):
            SqlParseExtendedProtocolCase.record_results(context, SimpleNamespace(stdout=bad, returncode=0))
        assert any(record['status'] == 'FAIL' for _, record in context.records)


def test_report_explains_connection_and_distinct_transactions(tmp_path):
    path = Path(__file__).parents[1] / 'products/fbasecman/reports/parser.py'
    spec = importlib.util.spec_from_file_location('cman_report_transactions', path)
    parser = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(parser)
    (tmp_path / 'report.txt').write_text('结论: PASS\n')
    report = parser.parse_report('ha_commands.sql_parse_extended_protocol', tmp_path)
    scope = report['execution_scope']
    assert '同一个 JDBC Connection' in scope['connection']
    assert [row['stage'] for row in scope['transactions']] == ['自动提交阶段', '事务 1', '事务 2', '事务 3']
    assert 'rollback()' in scope['transactions'][1]['boundary']
    assert '实际执行回滚' in scope['transactions'][2]['boundary']
    assert '新事务' in scope['conclusion']
    assert report['execution_scope_source'] == 'current_catalog'


def test_report_runtime_uses_client_timezone(tmp_path):
    from platform_regress.runtime import ReportRuntime, ReportSpec
    runtime = ReportRuntime(
        tmp_path, ReportSpec('case', 'ha_commands.case', 'HA', 'ha_commands'),
        case_dir=tmp_path / 'case', lock_dir=tmp_path / 'locks', context_data={})
    assert runtime.started_at.strftime('%z') == '+0800'


def test_readable_observations_preserve_actual_values():
    from products.fbasecman.reports.observations import readable_jdbc_observations
    assert readable_jdbc_observations('PARAM_VALUE=420') == '参数查询返回值：420'
    assert readable_jdbc_observations('ROLLBACK_ERROR=22012；ROLLBACK_VALUE=42') == (
        '回滚前的 SQL 错误码：22012（除零错误）\n回滚后新事务的查询返回值：42')
    assert readable_jdbc_observations('COMMIT_ERROR=08006') == '结束失败事务前的 SQL 错误码：08006'
    assert readable_jdbc_observations('arbitrary program output') == 'arbitrary program output'


def test_savepoint_failure_keeps_precise_failed_step(tmp_path):
    from platform_regress.sdk import CaseContext
    from products.fbasecman.native import SavepointRecoveryCase
    expected = {'begin': (None, 'T', 'BEGIN', None), 'savepoint': (None, 'T', 'SAVEPOINT', None),
                'division': ('22012', 'E', None, None), 'aborted_select': ('25P02', 'E', None, None),
                'rollback_to': (None, 'T', 'ROLLBACK', None), 'recovery_select': (None, 'T', 'SELECT 1', '9'),
                'cleanup': (None, 'I', 'ROLLBACK', None)}
    records = []
    for variant in ('direct_recovery', 'after_local_25p02'):
        order = ['begin', 'savepoint', 'division']
        if variant == 'after_local_25p02':
            order.append('aborted_select')
        for step in order + ['rollback_to', 'recovery_select', 'cleanup']:
            error, state, tag, value = expected[step]
            records.append({'variant': variant, 'step': step, 'sql': step,
                            'sqlstate': error, 'ready': state, 'command_tag': tag, 'value': value})
    records[4]['value'] = '8'
    import pytest
    context = CaseContext('sql_parse.savepoint_recovery_after_local_25p02', tmp_path)
    with pytest.raises(AssertionError):
        SavepointRecoveryCase.record_protocol(context, records)
    events = [json.loads(line) for line in (tmp_path / 'events.jsonl').read_text().splitlines()]
    failed = next(event['payload'] for event in events if event['kind'] == 'step.finished' and event['payload']['status'] == 'FAIL')
    assert '原事务' in failed['title']
    assert '查询值=9' in failed['details']['expected']
    assert '查询值=8' in failed['details']['actual']
    assert failed['details']['analysis']


def test_shared_query_check_records_result_not_only_telemetry(tmp_path):
    from types import SimpleNamespace
    from platform_regress.sdk import CaseContext
    from products.fbasecman.native import _expect
    context = CaseContext('guc.test', tmp_path)
    _expect(context, 'query', '读取 work_mem', '4MB', lambda out: out == '4MB',
            lambda: SimpleNamespace(stdout='4MB', returncode=0))
    events = [json.loads(line) for line in (tmp_path / 'events.jsonl').read_text().splitlines()]
    details = next(event['payload']['details'] for event in events if event['kind'] == 'step.finished')
    assert '4MB' in details['actual']
    assert details['analysis']
    assert details['intent'] == 'verify'


def test_node_weight_does_not_match_other_numeric_fields():
    from products.fbasecman.native import SetNodeWeightIdempotentCase
    header = 'node_name|cluster_name|database|host|port|application|identifier|state|check|weight|other\n'
    row = header + 'pg_3|cluster|postgres|127.0.0.1|15012||identifier|active|auto|110|10'
    assert SetNodeWeightIdempotentCase.node_weights(row, 'pg_3') == ['110']
    assert SetNodeWeightIdempotentCase.node_weights(row.replace('|110|10', '|10|10'), 'pg_3') == ['10']
    assert SetNodeWeightIdempotentCase.node_weights(row.replace('pg_3', 'pg_30'), 'pg_3') == []


def test_guc_script_uses_one_client_with_separate_statement_messages():
    from products.fbasecman.native import _guc_script, guc_case
    class Context:
        environment = {}
        def command(self, argv, **kwargs):
            assert '-c' not in argv
            assert 'ON_ERROR_STOP=1' in argv
            assert kwargs['input_text'].count('DISCARD ALL;') == 1
            assert 'SHOW work_mem;' in kwargs['input_text']
            return 'single client invocation'
    case = guc_case('discard_all_sql_parse')
    script = next(sql for _, _, sql, _ in case.actions if sql and 'DISCARD ALL;' in sql)
    assert _guc_script(Context(), 'psql', 12345, script) == 'single client invocation'
    _, _, _, predicate = guc_case('reset_all_sql_parse').actions[1]
    assert not predicate('32MB\n10s\n4MB\n10s')
    assert predicate('32MB\n10s\n4MB\n0')
