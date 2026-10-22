"""Explicit physical role changes; preview and execution use actual instance roles."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone

ACTIONS = {'deployment.failover', 'deployment.switchover', 'deployment.rejoin', 'deployment.restore'}


def preview(settings, environment, group):
    args = [sys.executable, str(Path(__file__).resolve()), '--engine-root', str(settings.pgcluster_root),
            '--config', environment['deployment_config'], '--group', group, '--preview']
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired as exc:
        raise ValueError('读取实际主备角色超时') from exc
    if result.returncode:
        raise ValueError(result.stderr.strip() or result.stdout.strip() or '无法读取实际主备角色')
    return json.loads(result.stdout)



def partial_rebuild_claim(runtime, group, name):
    """Only our checkpoint may authorize resuming an incomplete, stopped clone."""
    state = runtime._state()
    claim = state.get('role_rebuilds', {}).get(group)
    if not claim or claim.get('node') != name or claim.get('phase') != 'copying':
        return False
    inst = runtime.config.instance(name)
    if (claim.get('data_dir') != inst['data_dir'] or
            claim.get('host') != inst['host_config']['address'] or
            claim.get('source') != state.get('primaries', {}).get(group)):
        return False
    result = runtime.executor.run(['sh', '-c',
        'test ! -e "$1/postmaster.pid" && { test -d "$1" || test -d "$2"; }',
        'sh', inst['data_dir'], str(Path(inst['data_dir']).parent)],
        host=runtime._host(inst), check=False)
    return result.returncode == 0


def snapshot(runtime, group):
    cluster = runtime.config.streaming_clusters.get(group)
    if not cluster:
        raise ValueError('复制组不存在')
    members = [cluster['primary']] + [s['instance'] for s in cluster.get('standbys', [])]
    nodes = []
    for name in members:
        inst = runtime.config.instance(name)
        status = runtime.status_instance(name)
        running = status['running']
        if running is None or status.get('known') is False:
            if partial_rebuild_claim(runtime, group, name):
                running = False
            else:
                raise ValueError(f'{name} 的运行状态未知，不能确定旧主是否已停止')
        recovery = runtime._psql(name, 'SELECT pg_is_in_recovery()', tuples=True) if running else None
        if recovery not in ('t', 'f', None):
            raise ValueError(f'{name} 返回无法识别的数据库角色')
        nodes.append({'id': name, 'host': inst['host_config']['address'], 'port': inst['port'],
                      'data_dir': inst['data_dir'], 'running': running,
                      'role': 'standby' if recovery == 't' else 'primary' if recovery == 'f' else 'stopped'})
    primaries = [n['id'] for n in nodes if n['role'] == 'primary']
    if len(primaries) > 1:
        raise ValueError('检测到多个可写主库，禁止自动切换')
    current = primaries[0] if primaries else runtime._primary(group)
    if current not in members:
        raise ValueError('记录的当前主库不属于本复制组')
    previous = runtime._state().get('failovers', {}).get(group, {})
    old = previous.get('old_primary')
    return {'group': group, 'current_primary': current, 'original_primary': cluster['primary'],
            'old_primary': old if old and old != current else None,
            'rejoin_required': bool(old and old != current and not previous.get('rejoined')), 'nodes': nodes}


def validate_request(info, action, selected, expected):
    if info['current_primary'] != expected:
        raise ValueError('当前主库已变化，请刷新后重新确认')
    nodes = {n['id']: n for n in info['nodes']}
    if selected not in nodes:
        raise ValueError('所选节点不属于此复制组')
    if action == 'deployment.rejoin':
        if not info.get('rejoin_required') or selected != info['old_primary'] or selected == expected:
            raise ValueError('只能重建此组记录的旧主库，不能重建当前主库')
        if nodes[expected]['role'] != 'primary':
            raise ValueError('当前主库不可写，不能重建旧主')
    else:
        if nodes[expected]['role'] not in {'primary', 'stopped'}:
            raise ValueError('确认的旧主实际是备库，请刷新角色后重试')
        if selected == expected or nodes[selected]['role'] != 'standby':
            raise ValueError('所选目标必须是正在运行的真实备库')
        if action == 'deployment.restore' and selected != info['original_primary']:
            raise ValueError('切回原主库的目标必须是配置声明的原主库')
    return nodes



def mmr_contexts(runtime, group):
    return [(name, cluster, member) for name, cluster in runtime.config.mmr_clusters.items()
            for member in cluster['members'].values() if member['streaming_cluster'] == group]


def update_mmr_endpoints(runtime, group, old_primary, new_primary, emit):
    """Use MMR's supported API on each current writable member, preserving credentials."""
    from psycopg.conninfo import conninfo_to_dict, make_conninfo
    def literal(value):
        return "'"+str(value).replace("'", "''")+"'"
    destination = runtime.config.instance(new_primary)
    original = runtime.config.instance(old_primary)
    suffix = ' '+make_conninfo(host=destination['host_config']['address'], port=destination['port'])
    def matches(conn, instance):
        host = conn.get('host')
        configured = instance['host_config']['address']
        same_host = host == configured or (runtime.executor.is_local(host) and runtime.executor.is_local(configured))
        return same_host and str(conn.get('port')) == str(instance['port'])
    for name, cluster, member in mmr_contexts(runtime, group):
        database = cluster.get('database', 'postgres')
        for observer in cluster['members'].values():
            node = runtime._primary(observer['streaming_cluster'])
            dsn = runtime._psql(node, 'SELECT node_dsn FROM fdd.mmr_node WHERE node_name='+literal(member['node_name']), database, True)
            conn = conninfo_to_dict(dsn)
            if conn.get('dbname') != database:
                raise ValueError('MMR 成员数据库身份不一致，未改写连接')
            if matches(conn, destination):
                emit(f"MMR {name}/{member['node_name']} 在 {node} 上已指向新主")
                continue
            if not matches(conn, original):
                raise ValueError(f"MMR {member['node_name']} 在 {node} 上的连接不是确认过的旧主，未改写")
            # Read/assemble secret-bearing conninfo inside the server, never in logged SQL.
            runtime._psql(node, 'DO $platform_role$ DECLARE connection text; BEGIN '
                'SELECT node_dsn INTO STRICT connection FROM fdd.mmr_node WHERE node_name='+literal(member['node_name'])+'; '
                "EXECUTE format('SELECT fdd.alter_node_interface(%L,%L,false)', "+literal(member['node_name'])+
                ', connection || '+literal(suffix)+'); END $platform_role$', database)
            refreshed = conninfo_to_dict(runtime._psql(node,
                'SELECT node_dsn FROM fdd.mmr_node WHERE node_name='+literal(member['node_name']), database, True))
            if not matches(refreshed, destination):
                raise ValueError('MMR 成员连接更新后未指向新主')


def execute(runtime, action, group, selected, expected, emit):
    """A completed promotion is persisted before any destructive rejoin work."""
    if action not in ACTIONS:
        raise ValueError('未知角色操作')
    steps = ['核对实际角色与目标']
    if action != 'deployment.rejoin':
        steps += ['确认复制同步位置', '停止旧主库', '提升选中备库', '验证新主可写并登记角色']
    if mmr_contexts(runtime, group):
        steps += ['同步MMR成员连接']
    if action != 'deployment.failover':
        steps += ['调整其余备库上游', '重建旧主为备库', '验证物理复制']
    steps += ['检查关联集群健康']
    emit('ROLE_PLAN '+json.dumps(steps, ensure_ascii=False))
    completed = 0

    def step(title, function):
        nonlocal completed
        started = time.monotonic()
        def report(status, error=None):
            emit('ROLE_STEP ' + json.dumps({'title': title, 'status': status, 'index': completed+1,
                 'total': len(steps), 'at': datetime.now(timezone.utc).isoformat(),
                 'elapsed': round(time.monotonic()-started, 3), 'error': error}, ensure_ascii=False))
        report('running')
        emit(f'[{completed}/{len(steps)}] {title}')
        try:
            result = function()
        except Exception as exc:
            report('failed', str(exc))
            raise
        report('passed')
        completed += 1
        emit(f'[{completed}/{len(steps)}] 已完成：{title}')
        return result

    def preflight():
        info = snapshot(runtime, group)
        nodes = validate_request(info, action, selected, expected)
        for name in (expected, selected):
            if action == 'deployment.rejoin' and name == selected and partial_rebuild_claim(runtime, group, name):
                # The old directory was validated and stopped before our own interrupted clone.
                inst = runtime.config.instance(name)
                runtime.executor.run(['mkdir', '-p', inst['data_dir']], host=runtime._host(inst))
                runtime.executor.write_text(runtime._host(inst), runtime._marker(name), json.dumps({'node':name})+'\n')
            if not runtime._managed(name):
                raise ValueError(f'{name} 的数据目录不是平台受管目录')
        if action != 'deployment.rejoin':
            for name, cluster, _ in mmr_contexts(runtime, group):
                if nodes[expected]['running']:
                    try:
                        runtime.verify_target('mmr.'+name)
                    except Exception as exc:
                        raise ValueError('切换前 MMR 健康检查未通过，尚未停止旧主：'+str(exc)) from exc
                else:
                    emit('旧主已停止，无法完整执行 MMR 前置检查；提升后将更新成员连接并复检。')
        return info, nodes

    info, nodes = step(steps[0], preflight)
    new_primary = expected if action == 'deployment.rejoin' else selected
    old_primary = selected if action == 'deployment.rejoin' else expected

    def wait_replay():
        if not nodes[expected]['running']:
            emit('旧主已停止：无法查询其最终 WAL 位置，将校验备库接收与回放位置；不承诺零数据丢失。')
            lsn = runtime._psql(selected, 'SELECT pg_last_wal_receive_lsn()', tuples=True)
        else:
            source_id = runtime._psql(expected, 'SELECT system_identifier FROM pg_control_system()', tuples=True)
            target_id = runtime._psql(selected, 'SELECT system_identifier FROM pg_control_system()', tuples=True)
            if not source_id or source_id != target_id:
                raise ValueError('选中备库与旧主不属于同一物理数据库集群')
            lsn = runtime._psql(expected, 'SELECT pg_current_wal_flush_lsn()', tuples=True)
        if not re.fullmatch(r'[0-9A-F]+/[0-9A-F]+', lsn, re.I):
            raise ValueError('无法确认 WAL 同步位置，终止升主')
        end = time.monotonic()+60
        while True:
            replayed = runtime._psql(selected, "SELECT NOT pg_is_wal_replay_paused() AND pg_last_wal_replay_lsn() >= '%s'::pg_lsn" % lsn, tuples=True)
            if replayed == 't':
                return
            if time.monotonic() >= end:
                raise ValueError('等待备库回放 WAL 超时，未停止旧主')
            time.sleep(1)

    def stop_old():
        # Re-check immediately before mutation; never stop an unexpected primary.
        validate_request(snapshot(runtime, group), action, selected, expected)
        if runtime.status_instance(expected)['running']:
            runtime.stop_instance(expected)
        status = runtime.status_instance(expected)
        if status['running'] is not False or status.get('known') is False:
            raise ValueError('旧主未确认停止，禁止提升备库')

    def promote():
        status = runtime.status_instance(expected)
        if status['running'] is not False or status.get('known') is False:
            raise ValueError('旧主在升主前状态变化，禁止继续提升')
        if runtime._live_role(selected) is not True:
            raise ValueError('选中节点不再是备库，终止升主')
        runtime._promote(selected)

    def register():
        if runtime._live_role(selected) is not False:
            raise ValueError('新主没有退出 recovery')
        state = runtime._state()
        state.setdefault('primaries', {})[group] = selected
        state.setdefault('failovers', {})[group] = {'old_primary': expected, 'new_primary': selected,
                                                    'rejoined': False}
        runtime._save_state(state)

    if action != 'deployment.rejoin':
        step('确认复制同步位置', wait_replay)
        step('停止旧主库', stop_old)
        step('提升选中备库', promote)
        step('验证新主可写并登记角色', register)

    if mmr_contexts(runtime, group):
        step('同步MMR成员连接', lambda: update_mmr_endpoints(runtime, group, old_primary, new_primary, emit))

    def rebuild():
        # Keep completed promotion when rebuild fails; a later rejoin resumes here.
        if runtime._live_role(new_primary) is not False:
            raise ValueError('新主不再可写，禁止重建')
        if runtime.status_instance(old_primary)['running']:
            runtime.stop_instance(old_primary)
        old_status = runtime.status_instance(old_primary)
        if old_status['running'] or old_status.get('known') is False:
            if not partial_rebuild_claim(runtime, group, old_primary):
                raise ValueError('旧主未确认停止，禁止开始重建')
        inst = runtime.config.instance(old_primary)
        state = runtime._state()
        state.setdefault('role_rebuilds', {})[group] = {'node':old_primary,'source':new_primary,
            'host':inst['host_config']['address'],'data_dir':inst['data_dir'],'phase':'copying'}
        runtime._save_state(state)
        slot = runtime._standby_slot(group, old_primary)
        runtime._rebuild_standby(group, new_primary, old_primary, slot)


    remaining = [n['id'] for n in info['nodes'] if n['id'] not in {old_primary, new_primary}]

    def repoint_remaining():
        for name in remaining:
            if runtime._live_role(name) is not True:
                raise ValueError(f'其余节点 {name} 不是真实在线备库，未自动重建该节点')
            runtime._write_upstream(group, name, new_primary)
            runtime._psql(name, 'SELECT pg_reload_conf()')
            end = time.monotonic()+30
            while runtime._psql(name, "SELECT status FROM pg_stat_wal_receiver WHERE status='streaming'", tuples=True) != 'streaming':
                if time.monotonic() >= end:
                    raise ValueError(f'{name} 无法跟随新主，旧主尚未开始重建；请检查 WAL 保留和复制连接')
                time.sleep(1)

    def verify_replication():
        end = time.monotonic()+60
        while True:
            waiting = []
            for name in [old_primary] + remaining:
                if runtime._live_role(name) is not True:
                    raise ValueError(f'{name} 不是备库')
                result = runtime._psql(name, "SELECT status FROM pg_stat_wal_receiver WHERE status='streaming'", tuples=True)
                if result != 'streaming' or not runtime._upstream_matches(group, name, new_primary):
                    waiting.append(name)
            if not waiting:
                state = runtime._state()
                state.setdefault('failovers', {}).setdefault(group, {})['rejoined'] = True
                state.setdefault('role_rebuilds', {}).pop(group, None)
                runtime._save_state(state)
                return
            if time.monotonic() >= end:
                raise ValueError('备库未恢复到新主的 streaming 复制：'+','.join(waiting))
            time.sleep(1)

    if action != 'deployment.failover':
        step('调整其余备库上游', repoint_remaining)
        step('重建旧主为备库', rebuild)
        step('验证物理复制', verify_replication)

    def associated_health():
        # MMR and proxy routing are separate from physical promotion: verify and report.
        for name, cluster in runtime.config.mmr_clusters.items():
            if any(m['streaming_cluster'] == group for m in cluster['members'].values()):
                deadline = time.monotonic()+30
                while True:
                    try:
                        runtime.verify_target('mmr.'+name)
                        break
                    except Exception:
                        if time.monotonic() >= deadline:
                            raise
                        time.sleep(1)
        emit('物理角色操作已完成；若部署包含外部代理，代理路由需按其监控状态确认。')
    step('检查关联集群健康', associated_health)
    return {'new_primary': new_primary, 'old_primary': old_primary,
            'old_primary_state': 'stopped' if action == 'deployment.failover' else 'standby'}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--engine-root', required=True)
    parser.add_argument('--config', required=True)
    parser.add_argument('--group', required=True)
    parser.add_argument('--preview', action='store_true')
    parser.add_argument('--action', choices=sorted(ACTIONS))
    parser.add_argument('--selected')
    parser.add_argument('--expected')
    args = parser.parse_args()
    sys.path.insert(0, args.engine_root)
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from pgclusterlib.config import load
    from pgclusterlib.runtime import Runtime
    from pgclusterlib.locking import configuration_lock
    from pgclusterlib.executor import Executor, LOCAL_HOSTS
    from platform_app.resources import local_addresses
    LOCAL_HOSTS.update(local_addresses())
    os.environ.setdefault('PGCONNECT_TIMEOUT', '5')
    config = load(args.config)
    config.validate('streaming.'+args.group)
    if args.preview:
        try:
            print(json.dumps(snapshot(Runtime(config), args.group), ensure_ascii=False))
        except Exception as exc:
            print(str(exc), file=sys.stderr)
            raise SystemExit(1)
        return

    def emit(line):
        # Connection secrets must not enter task logs.
        print(re.sub(r"(?i)(password\s*=\s*)(?:'[^']*'|\S+)", r'\1***', line), flush=True)

    class LiveExecutor(Executor):
        def run(self, argv, host='local', cwd=None, check=True, stdin=None):
            from pgclusterlib.executor import shell_join
            from pgclusterlib.errors import OperationError
            argv = [str(a) for a in argv]
            remote = host != 'local' and not self.is_local(host)
            actual = self._ssh_argv(host, argv) if remote else argv
            if Path(argv[0]).name == 'pg_basebackup':
                argv += ['--progress', '--verbose', '--checkpoint=fast']
                actual = self._ssh_argv(host, argv) if remote else argv
            emit(f'COMMAND [{host}] {shell_join(argv)}')
            process = subprocess.Popen(actual, cwd=None if remote else cwd, text=True,
                                       stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            output = {'stdout': [], 'stderr': []}
            def consume(pipe, name):
                for line in iter(pipe.readline, ''):
                    output[name].append(line)
                    emit(name.upper()+': '+line.rstrip())
                pipe.close()
            threads=[threading.Thread(target=consume, args=(process.stdout,'stdout')),
                     threading.Thread(target=consume, args=(process.stderr,'stderr'))]
            for thread in threads: thread.start()
            if stdin is not None:
                process.stdin.write(stdin);process.stdin.close()
            code=process.wait()
            for thread in threads: thread.join()
            emit(f'EXIT_CODE: {code}')
            result=subprocess.CompletedProcess(actual,code,''.join(output['stdout']),''.join(output['stderr']))
            if check and code:
                raise OperationError(f'命令失败：{shell_join(argv)}；退出码 {code}；{result.stderr.strip()}')
            return result

    executor=LiveExecutor(hosts={h['address']:h.get('ssh') for h in config.hosts.values() if h.get('ssh')})
    runtime=Runtime(config,executor,progress=lambda msg:emit('==> '+msg))
    try:
        with configuration_lock(config):
            result=execute(runtime,args.action,args.group,args.selected,args.expected,emit)
        emit('ROLE_RESULT '+json.dumps(result,ensure_ascii=False))
        emit(f"操作完成：主库 {result['new_primary']}；旧主 {result['old_primary']}" +
             (' 保持停止，可另行重建为备库。' if result['old_primary_state']=='stopped' else ' 已重建为备库并恢复 streaming 复制。'))
    except Exception as exc:
        emit('ROLE_ERROR '+str(exc))
        try: emit('ROLE_ACTUAL '+json.dumps(snapshot(runtime,args.group),ensure_ascii=False))
        except Exception as inspect_error:emit('无法确认最终状态：'+str(inspect_error))
        raise SystemExit(1)


if __name__ == '__main__':
    main()
