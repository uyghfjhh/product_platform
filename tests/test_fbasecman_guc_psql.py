"""psql evidence must distinguish real switching, pool reuse and clean parameters."""

import re
from types import SimpleNamespace

import pytest
from platform_regress.reporting.case_report import write_case_artifacts
from platform_regress.sdk import CaseContext

from products.fbasecman import guc_alignment_native as alignment
from products.fbasecman.guc_psql import (
    ADDRESS_SQL,
    PsqlGucChecks,
    observe,
    parse_observations,
)


def table(headers, values):
    return ' | '.join(headers) + '\n' + '+'.join('-' * 24 for _ in headers) + '\n' + ' | '.join(values) + '\n(1 row)\n\n'


def output(stage='sample', app='psql', extra='1', port='15432', pid='101', recovery='f'):
    return ('\\echo __GUC_STAGE__:' + stage + '\n__GUC_STAGE__:' + stage + '\n'
            + 'SHOW extra_float_digits;\n' + table(['extra_float_digits'], [extra])
            + 'SHOW application_name;\n' + table(['application_name'], [app])
            + ADDRESS_SQL + '\n' + table(['inet_server_addr', 'inet_server_port'], ['127.0.0.1', port])
            + table(['pg_backend_pid', 'pg_is_in_recovery'], [pid, recovery])
            + '\\echo __GUC_END__\n__GUC_END__\n')


def runner(tmp_path, scenario='psql_client_reuse'):
    plan = alignment.CheckPlan('mmr', 'transaction', True, 'psql', scenario)
    return alignment.ScenarioRunner(CaseContext('guc.backend_redeploy_hint', tmp_path), plan, 'hint', 6432)


def test_psql_aligned_output_and_echoed_sql_are_distinguished():
    values = parse_observations(output() + output('second', app='321', extra='3', port='15433'))
    assert list(values) == ['sample', 'second']
    assert values['sample'] == {'application_name': 'psql', 'extra_float_digits': '1',
                                'host': '127.0.0.1', 'port': '15432', 'pid': '101', 'recovery': 'false'}
    assert values['second']['extra_float_digits'] == '3'


@pytest.mark.parametrize('bad_output', [
    output() + output(),
    output().replace('101 | f', '101 | invalid'),
    output().replace('psql\n(1 row)', 'psql\nother\n(2 rows)'),
    output().replace('SHOW application_name;\n' + table(['application_name'], ['psql']), ''),
])
def test_psql_incomplete_duplicate_and_invalid_tables_do_not_pass(bad_output):
    with pytest.raises(AssertionError):
        parse_observations(bad_output)


@pytest.mark.parametrize('case', ['psql_savepoint_switch', 'psql_client_reuse'])
def test_new_psql_cases_cover_topologies_and_reserve_settings(case):
    plans = [p for p in alignment.make_plan('backend_redeploy') if p.scenario == case]
    assert {(p.topology, p.reserve) for p in plans} == {('mmr', True), ('mmr', False), ('replication', True), ('replication', False)}
    assert all(p.protocol == 'psql' and p.pool == 'transaction' and p.enable_sync for p in plans)


def test_different_pid_on_same_node_is_not_a_read_write_switch(tmp_path):
    checks = PsqlGucChecks(runner(tmp_path))
    checks.runner.verify_route = lambda *args: None
    before = parse_observations(output())['sample']
    after = {**before, 'pid': '102'}
    with pytest.raises(AssertionError, match='确认切换'):
        checks.check_switch('确认切换', before, after, 'read')


@pytest.mark.parametrize('fault', [None, 'new_backend', 'polluted_float', 'polluted_application'])
def test_client_exit_requires_same_backend_and_both_clean_parameters(tmp_path, fault):
    checks = PsqlGucChecks(runner(tmp_path))
    checks.runner.route_config = lambda: ([{'名称': 'primary', '集群': 'write', '主机': ['127.0.0.1'], '端口': 15432}], 'write', None)
    checks.runner.verify_route = lambda *args: None
    calls = []

    def command(argv, *, input_text, **kwargs):
        calls.append((argv, input_text))
        if '__GUC_STAGE__:default' in input_text:
            text = output('default')
        elif '__GUC_STAGE__:old_client' in input_text:
            assert "set application_name = '321';" in input_text
            assert 'SET extra_float_digits = 3;' in input_text
            text = output('old_client', app='321', extra='3')
        else:
            assert '__GUC_STAGE__:old_client' in calls[-2][1]
            assert calls[-2][1].endswith('\\q\n')
            text = output('new_client', app='321' if fault == 'polluted_application' else 'psql',
                          extra='3' if fault == 'polluted_float' else '1',
                          pid='102' if fault == 'new_backend' else '101')
        return SimpleNamespace(returncode=0, stdout=text, stderr='')

    checks.runner.context.command = command
    if fault:
        with pytest.raises(AssertionError):
            checks.client_reuse()
        assert checks.runner.results[-1]['passed'] is False
    else:
        checks.client_reuse()
        assert checks.runner.results[-1]['passed'] is True
    assert all('-a' in argv and '-t' not in argv for argv, _ in calls)
    assert all(ADDRESS_SQL in script and 'SHOW port' not in script for _, script in calls)


def test_report_txt_keeps_psql_tables_and_explicit_switch_analysis(tmp_path):
    checks = PsqlGucChecks(runner(tmp_path))
    raw = output()
    checks.runner.context.command = lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=raw, stderr='')
    values = checks.run_client('A', observe('sample'))['sample']
    checks.runner.verify_route = lambda *args: None
    checks.check_switch('写到读切换', values, {**values, 'port': '15433'}, 'read')
    context = checks.runner.context
    result = SimpleNamespace(target=context.target, verdict='PASS', reason=None,
                             execution_id=context.execution_id, duration_seconds=0)
    write_case_artifacts(context, result, {}, purpose='psql 保存点及连接复用测试')
    text = (tmp_path / 'report.txt').read_text()
    for line in raw.splitlines():
        assert line in text
    assert 'SHOW extra_float_digits;\n' in text
    assert 'psql_output' not in text
    assert '读写切换成功' in text
    assert '判定: PASS' in text


@pytest.mark.parametrize('fault', [None, 'stale_on_read', 'same_node'])
def test_savepoint_paths_preserve_values_and_require_real_node_switches(tmp_path, fault):
    checks = PsqlGucChecks(runner(tmp_path, 'psql_savepoint_switch'))
    checks.runner.verify_route = lambda *args: None
    scripts = []

    def command(argv, *, input_text, **kwargs):
        scripts.append(input_text)
        parts = []
        for stage in re.findall(r'^\\echo __GUC_STAGE__:(\w+)$', input_text, re.M):
            committed = stage.startswith('committed') or stage == 'before_commit'
            app = 'psql' if stage == 'initial' else '321' if committed else '123'
            extra = '3' if committed or stage == 'before_rollback' else '1'
            read = stage.endswith('_read')
            if fault == 'stale_on_read' and stage == 'committed_read':
                extra = '1'
            parts.append(output(stage, app=app, extra=extra,
                                port='15433' if read and fault != 'same_node' else '15432'))
        return SimpleNamespace(returncode=0, stdout=''.join(parts), stderr='')

    checks.runner.context.command = command
    if fault:
        with pytest.raises(AssertionError):
            checks.savepoint_switch()
    else:
        checks.savepoint_switch()
    if fault == 'stale_on_read':
        assert any('读写切换成功' in (row['analysis'] or '') for row in checks.runner.results)
    script = scripts[0]
    assert "set application_name = '123';" in script
    assert " set application_name = '321';" in script
    assert script.count('rollback to savepoint sp1;') == 1
    assert script.count('SAVEPOINT sp1;') == 2
    assert script.count('SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY;') == 2
    assert 'SHOW port' not in script
