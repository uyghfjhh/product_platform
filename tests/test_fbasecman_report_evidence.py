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


def test_structured_guc_facts_are_readable_and_do_not_break_report_topology(tmp_path):
    from products.fbasecman.reports import parser
    (tmp_path / 'report.txt').write_text('结论: PASS\n验证目的:\n  GUC 参数值验证\n')
    from platform_regress.sdk import CaseContext
    context = CaseContext('guc.extended_boundary_sql_parse', tmp_path)
    context.step('wire', 'Q 参数观测', status='PASS', details={
        'intent': 'verify', 'expected': {'rows': [['8MB']]},
        'actual': {'rows': [['8MB']], 'ready': ['I']},
        'command': 'Q：SHOW work_mem',
    })
    value = parser.parse_report('guc.extended_boundary_sql_parse', tmp_path)
    step = next(s for s in value['steps'] if 'Q 参数观测' in s['title'])
    assert isinstance(step['actual'], str)
    assert '8MB' in step['actual']
    assert step['structured_actual']['rows'] == [['8MB']]
    assert isinstance(value['execution_scope']['transactions'], list)


def test_guc_scalar_actual_is_display_text_not_boolean(tmp_path):
    from platform_regress.sdk import CaseContext

    from products.fbasecman.reports import parser
    (tmp_path / 'report.txt').write_text('结论: PASS\n验证目的:\n  缓存边界\n')
    context = CaseContext('guc.extended_boundary_sql_parse', tmp_path)
    context.step('boolean-fact', '确认正式缓存未改变', details={
        'intent': 'verify', 'expected': True, 'actual': True, 'command': '比较真实缓存快照'})
    result = parser.parse_report('guc.extended_boundary_sql_parse', tmp_path)
    step = next(s for s in result['steps'] if s['title'] == '确认正式缓存未改变')
    assert step['actual'] == 'True'
    assert step['structured_actual'] is True


def test_guc_protocol_observations_are_actions_but_errors_stay_visible():
    from products.fbasecman.reports.parser import _present_guc_alignment_steps
    normal = {'title': 'Q：SELECT 42::int', 'command': 'Q：SELECT 42::int', 'status': 'PASS',
              'expected': {'sqlstates': [], 'ready': ['I'], 'tags': ['SELECT 1']},
              'actual': {'received': ['T', 'D', 'C', 'Z'], 'rows': [['42']]}, 'intent': 'verify'}
    failed = {**normal, 'status': 'FAIL'}
    values = {'title': '参数匹配', 'status': 'PASS', 'expected': {'work_mem': '32MB'},
              'actual': {'work_mem': '8MB'}, 'intent': 'verify'}
    _present_guc_alignment_steps([normal, failed, values])
    assert normal['intent'] == 'action'
    assert failed['intent'] == 'verify'
    assert '期望 32MB，实际 8MB' in values['analysis']


def test_guc_same_query_parameter_checks_are_grouped_without_hiding_failure():
    from products.fbasecman.reports.parser import _present_guc_alignment_steps
    steps = [
        {'title':'MMR/sql_parse/tx_set_commit：Q：COMMIT', 'command':'Q：COMMIT', 'status':'PASS',
         'expected':{'tags':['COMMIT']}, 'actual':{'received':['C','Z']}, 'intent':'verify'},
        {'title':'MMR/sql_parse/tx_set_commit：work_mem', 'status':'PASS', 'intent':'verify',
         'expected':{'work_mem':'32MB'}, 'actual':{'work_mem':'32MB','后端':['h','1','2']}},
        {'title':'MMR/sql_parse/tx_set_commit：statement_timeout', 'status':'FAIL', 'intent':'verify',
         'expected':{'statement_timeout':'7s'}, 'actual':{'statement_timeout':'0','后端':['h','1','2']}},
    ]
    result=_present_guc_alignment_steps(steps)
    assert len(result)==2
    assert result[0]['intent']=='action'
    check=result[1]
    assert check['status']=='FAIL'
    assert check['expected']=={'work_mem':'32MB','statement_timeout':'7s'}
    assert check['assertion']['passed'] is False
    assert len(check['grouped_checks'])==2
    assert '提交事务后' in check['title']


def test_guc_summary_omits_nested_steps_and_success_analysis_does_not_repeat_values():
    from products.fbasecman.reports.parser import _present_guc_alignment_steps
    summary = {'title': 'mmr/hint/tx_set_commit 子场景结论', 'status': 'PASS',
               'actual': {'pool': 'transaction', 'protocol': 'Q', 'status': 'PASS',
                          'steps': [{'key': 'wire-1', 'passed': True}]}}
    check = {'title': '参数检查', 'status': 'PASS', 'expected': {'work_mem': '32MB'},
             'actual': {'work_mem': '32MB'}, 'analysis': '期望32MB；实际32MB'}
    result = _present_guc_alignment_steps([summary, check])
    assert 'steps' not in result[0]['actual']
    assert result[1]['expected'] == {'work_mem': '32MB'}
    assert result[1]['actual'] == {'work_mem': '32MB'}
    assert result[1]['analysis'] == '参数值与期望一致。'


def test_guc_reuse_summary_displays_archived_values_and_backend_identity():
    from products.fbasecman.reports.parser import _present_guc_alignment_steps
    check = {'title': '确认 A 复用同一后端', 'intent': 'verify', 'status': 'PASS',
             'expected': 'host:5432:123', 'actual': 'host:5432:123',
             'evidence': ['artifacts/run/check-1-wire.json']}
    summary = {'title': 'mmr/hint/session_backend_redeploy 子场景结论', 'status': 'PASS',
               'actual': {'scenario': 'session_backend_redeploy', 'topology': 'mmr',
                          'pool': 'transaction', 'reserve': True, 'protocol': 'E', 'status': 'PASS',
                          'steps': [{'key': 'check-1', 'passed': True}]}}
    result = _present_guc_alignment_steps([check, summary])[-1]
    assert '非默认' in result['expected'] and 'A/B' in result['expected']
    assert result['actual']['请求方式'] == '扩展协议（解析、绑定、执行）'
    assert 'host:5432:123' in result['actual']['已记录验证'][0]
    assert '确认 A 复用同一后端' in result['actual']['已记录验证'][0]
    assert '1 项具体验证' in result['analysis']
    assert 'steps' not in result['actual']


def test_guc_skipped_summary_does_not_claim_unexecuted_scope_passed():
    from products.fbasecman.reports.parser import _present_guc_alignment_steps
    result = _present_guc_alignment_steps([{
        'title': 'mmr/hint/mode_owner_isolation 子场景结论', 'status': 'SKIPPED',
        'actual': {'scenario': 'mode_owner_isolation', 'status': 'SKIPPED', 'reason': '单账号范围排除', 'steps': []}}])[0]
    assert result['status'] == 'SKIPPED'
    assert result['actual']['原因'] == '单账号范围排除'
    assert '不能视为已验证' in result['expected']
    assert '不能据此补造' in result['analysis']
