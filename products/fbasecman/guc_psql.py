"""Real psql transcripts and assertions for savepoints and pooled connections."""

import re
import shlex

from products.fbasecman.guc_alignment_native import connection_facts, identity

ADDRESS_SQL = "SELECT pg_catalog.inet_server_addr(), pg_catalog.inet_server_port();"
IDENTITY_SQL = "SELECT pg_catalog.pg_backend_pid(), pg_catalog.pg_is_in_recovery();"
OBSERVE_SQL = "SHOW extra_float_digits;\nSHOW application_name;\n" + ADDRESS_SQL + "\n" + IDENTITY_SQL


def observe(stage):
    return f"\\echo __GUC_STAGE__:{stage}\n{OBSERVE_SQL}\n\\echo __GUC_END__\n"


def parse_observations(output):
    """Read actual aligned tables; echoed SQL is never treated as query data."""
    observations = {}
    columns = {
        ('extra_float_digits',): ('extra_float_digits',),
        ('application_name',): ('application_name',),
        ('inet_server_addr', 'inet_server_port'): ('host', 'port'),
        ('pg_backend_pid', 'pg_is_in_recovery'): ('pid', 'recovery'),
    }
    for match in re.finditer(r'^__GUC_STAGE__:([\w]+)\n(.*?)^__GUC_END__\s*$', output, re.M | re.S):
        stage, text = match.groups()
        if stage in observations:
            raise AssertionError(f'psql 重复查询阶段：{stage}')
        values = {}
        lines = text.splitlines()
        for index in range(1, len(lines) - 1):
            if not re.fullmatch(r'\s*-+(?:\+-+)*\s*', lines[index]):
                continue
            headers = tuple(item.strip() for item in lines[index - 1].split('|'))
            names = columns.get(headers)
            if names is None:
                continue
            row = [item.strip() for item in lines[index + 1].split('|')]
            if len(row) != len(names) or any(name in values for name in names):
                raise AssertionError(f'psql {stage} 查询表格不完整或重复')
            if index + 2 < len(lines) and not lines[index + 2].strip().startswith('('):
                raise AssertionError(f'psql {stage} 查询没有返回唯一一行')
            values.update(zip(names, row))
        if set(values) != {'extra_float_digits', 'application_name', 'host', 'port', 'pid', 'recovery'}:
            raise AssertionError(f'psql {stage} 缺少参数或后端查询结果：{values}')
        if values['recovery'] not in {'t', 'f'} or not values['pid'].isdigit():
            raise AssertionError(f'psql {stage} 后端身份无效：{values}')
        values['recovery'] = 'true' if values['recovery'] == 't' else 'false'
        observations[stage] = values
    return observations


class PsqlGucChecks:
    def __init__(self, runner):
        self.runner = runner

    def route(self, side):
        if self.runner.mode == 'hint':
            return f'SET SESSION CHARACTERISTICS AS TRANSACTION READ {side};\nBEGIN;\n'
        return f'BEGIN READ {side};\n'

    def run_client(self, label, script, *, host=None, port=None, database=None):
        runner = self.runner
        argv = [runner.context.environment.get('psql_bin', '/usr/bin/psql'), '-X', '-w', '-a',
                '-h', host or runner.host, '-p', str(port or runner.port), '-U', 'postgres',
                '-d', database or runner.database, '-v', 'ON_ERROR_STOP=1',
                '-P', 'format=aligned', '-P', 'pager=off', '-f', '-']
        runner.context.attach_text(f'{runner.plan.key}-{label}.sql', script)
        result = runner.context.command(argv, input_text=script, timeout_seconds=30)
        evidence = runner.context.attach_text(f'{runner.plan.key}-{label}-psql.txt', result.stdout + result.stderr)
        runner.verify(f'psql 客户端 {label} 输入输出', '脚本执行完成并正常退出（退出码 0）',
                      result.stdout + (f'\n标准错误：\n{result.stderr}' if result.stderr else ''),
                      result.returncode == 0, command=shlex.join(argv), intent='action',
                      analysis=f'psql 实际退出码 {result.returncode}。以上为真实 SQL 回显、命令标签和表格输出；'
                      '逐项参数、实际读写节点及复用结论见后续分析。')
        observations = parse_observations(result.stdout)
        required = re.findall(r'^\\echo __GUC_STAGE__:(\w+)$', script, re.M)
        runner.verify(f'psql 客户端 {label} 查询结果完整', required, list(observations),
                      list(observations) == required,
                      analysis='每个阶段必须同时返回两个 GUC、实际地址和端口、后端 PID 及恢复角色，缺失结果不能算通过。')
        for value in observations.values():
            value.update(client=label, observation_sql=OBSERVE_SQL, observation_evidence=evidence)
        return observations

    def check_values(self, stage, value, expected):
        actual = {**value, '后端': connection_facts(value)}
        self.runner.verify(stage, expected, actual,
                           all(value[name] == str(wanted) for name, wanted in expected.items()),
                           command=OBSERVE_SQL,
                           analysis='；'.join(f'{name}：预期 {wanted}，实测 {value[name]}' for name, wanted in expected.items()))

    def check_switch(self, label, before, after, side):
        self.runner.verify_route(after, side)
        previous = (before['host'], before['port'])
        current = (after['host'], after['port'])
        self.runner.verify(label, '读写角色正确且实际节点地址或端口发生变化',
                           {'切换前': connection_facts(before), '切换后': connection_facts(after)},
                           previous != current, command=ADDRESS_SQL,
                           analysis=f'切换前 {previous[0]}:{previous[1]}，切换后 {current[0]}:{current[1]}。'
                           + ('实际节点已改变，结合前项配置和角色校验，读写切换成功。' if previous != current else
                              '实际节点未改变，即使 SET 返回成功或 PID 改变，也不能判定读写切换成功。'))

    def savepoint_switch(self):
        script = self.route('WRITE') + 'SET extra_float_digits = 1;\n' + observe('initial')
        script += "set application_name = '123';\nSAVEPOINT sp1;\nSET extra_float_digits = 3;\n"
        script += observe('before_rollback') + 'rollback to savepoint sp1;\n'
        script += observe('after_rollback') + 'commit;\n'
        script += self.route('WRITE') + observe('rollback_committed') + 'COMMIT;\n'
        script += self.route('ONLY') + observe('rollback_read') + 'COMMIT;\n'
        script += self.route('WRITE') + observe('rollback_write') + 'COMMIT;\n'
        script += self.route('WRITE') + " set application_name = '321';\nSAVEPOINT sp1;\nSET extra_float_digits = 3;\n"
        script += observe('before_commit') + 'commit;\n'
        script += self.route('WRITE') + observe('committed_write') + 'COMMIT;\n'
        script += self.route('ONLY') + observe('committed_read') + 'COMMIT;\n'
        script += self.route('WRITE') + observe('committed_write_again') + 'COMMIT;\n\\q\n'
        values = self.run_client('A', script)
        self.runner.verify_route(values['initial'], 'write')
        # Route evidence remains visible even when a later GUC comparison fails.
        for start, read, write in (('rollback_committed', 'rollback_read', 'rollback_write'),
                                  ('committed_write', 'committed_read', 'committed_write_again')):
            self.runner.verify_route(values[start], 'write')
            self.check_switch('写→读：确认实际节点变化且读侧正确', values[start], values[read], 'read')
            self.check_switch('读→写：确认实际节点变化且写侧正确', values[read], values[write], 'write')
        self.check_values('建立 extra_float_digits=1 的事务内基线', values['initial'], {'extra_float_digits': '1'})
        for stage in ('before_rollback', 'after_rollback', 'rollback_committed', 'rollback_read', 'rollback_write',
                      'before_commit', 'committed_write', 'committed_read', 'committed_write_again'):
            second_transaction = stage in {'before_commit', 'committed_write', 'committed_read', 'committed_write_again'}
            label = {'before_rollback': '保存点后修改参数，回滚前', 'after_rollback': '回滚到保存点后，仍在事务内',
                     'rollback_committed': '保存点回滚后提交，写端复查', 'rollback_read': '保存点回滚后切到读端',
                     'rollback_write': '保存点回滚后切回写端', 'before_commit': '保存点后修改参数，不回滚',
                     'committed_write': '直接提交后，写端复查', 'committed_read': '直接提交后切到读端',
                     'committed_write_again': '直接提交后切回写端'}[stage]
            self.check_values(label, values[stage], {
                'application_name': '321' if second_transaction else '123',
                'extra_float_digits': '3' if second_transaction or stage == 'before_rollback' else '1',
            })
    def client_reuse(self):
        # Same psql startup and account as A/B: application_name has a client startup value.
        nodes, write_cluster, _ = self.runner.route_config()
        defaults = {}
        for node in nodes:
            if self.runner.plan.topology == 'mmr' and node['集群'] != write_cluster:
                continue
            endpoint = (node['主机'][0], str(node['端口']))
            values = self.run_client('default-' + node['名称'],
                                     'BEGIN;\n' + observe('default') + 'COMMIT;\n\\q\n',
                                     host=endpoint[0], port=endpoint[1], database='postgres')['default']
            if values['recovery'] == 'false':
                defaults[(values['host'], values['port'])] = values
        self.runner.verify('写侧默认值采集完成', '至少一个真实写节点的干净 psql 基线', list(defaults.values()), bool(defaults))
        applications = {value['application_name'] for value in defaults.values()}
        floats = {value['extra_float_digits'] for value in defaults.values()}
        app = '321' if '321' not in applications else '123'
        trial = next(value for value in ('3', '2', '1', '0') if value not in floats)
        script = self.route('WRITE') + f"set application_name = '{app}';\nSAVEPOINT sp1;\nSET extra_float_digits = {trial};\nCOMMIT;\n"
        script += self.route('WRITE') + observe('old_client') + 'COMMIT;\n\\q\n'
        old = self.run_client('A-exit', script)['old_client']
        self.runner.verify_route(old, 'write')
        self.check_values('旧客户端退出前确认已提交非默认参数', old,
                          {'application_name': app, 'extra_float_digits': trial})
        # run_client waits for psql exit before starting the replacement client.
        new = self.run_client('B-new', self.route('WRITE') + observe('new_client') + 'COMMIT;\n\\q\n')['new_client']
        self.runner.verify_route(new, 'write')
        reused = identity(old) == identity(new)
        self.runner.verify('旧客户端退出后，新客户端确实复用原后端连接', identity(old), identity(new), reused,
                           command=ADDRESS_SQL + '\n' + IDENTITY_SQL,
                           analysis=f'A 已正常退出；A 后端 {identity(old)}，B 后端 {identity(new)}。'
                           + ('地址、端口、PID 均一致，证明同一后端连接已交给 B。' if reused else
                              '后端连接不同，不能以新连接参数正常证明旧连接没有污染。'))
        baseline = defaults.get((new['host'], new['port']))
        self.runner.verify('复用后端具有预先采集的干净基线', '当前写节点的基线已归档', baseline, baseline is not None)
        self.check_values('新客户端未继承旧客户端的 GUC 参数', new,
                          {name: baseline[name] for name in ('extra_float_digits', 'application_name')})
