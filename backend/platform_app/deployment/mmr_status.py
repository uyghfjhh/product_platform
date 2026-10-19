"""Read-only, bounded MMR observations from verified writable physical instances."""
import json
from pathlib import Path
import subprocess
import sys
import time
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor

DIAGNOSTIC_SQL = 'SELECT * FROM fdd.show_node_info(true, false)'


def observe(runtime, group, members, database):
    result = {'physical_group': group, 'database': database, 'source': None, 'installed': False,
              'local_member': None, 'metadata': [], 'records': [], 'evidence': [], 'error': None}
    recorded = runtime._primary(group) if group in runtime.config.streaming_clusters else members[0]
    candidates = sorted(members, key=lambda name: name != recorded)

    def query(node, sql):
        inst = runtime.config.instance(node)
        argv = ['env', 'PGCONNECT_TIMEOUT=2', 'PGOPTIONS=-c statement_timeout=5000 -c lock_timeout=1000',
                runtime._bin(node, 'psql'), '-X', '-At', '-v', 'ON_ERROR_STOP=1', '-h',
                inst['host_config']['address'], '-p', str(inst['port']), '-U', 'postgres', '-d', database,
                '-c', sql]
        start = time.monotonic()
        proc = runtime.executor.run(argv, host=runtime._host(inst), check=False)
        entry = {'instance': node, 'sql': sql, 'result': None, 'error': None,
                 'duration_seconds': round(time.monotonic()-start, 3)}
        result['evidence'].append(entry)
        if proc.returncode:
            entry['error'] = proc.stderr.strip()[:1500] or '查询失败'
            raise ValueError(entry['error'])
        try:
            data = json.loads(proc.stdout.strip())
        except ValueError as exc:
            entry['error'] = '数据库返回了无法识别的 JSON'
            raise ValueError(entry['error']) from exc
        entry['result'] = data
        return data

    errors = []
    writable = []
    for node in candidates:
        try:
            identity = query(node, "SELECT json_build_object('in_recovery',pg_is_in_recovery(),"
                             "'installed',EXISTS(SELECT 1 FROM pg_extension WHERE extname='fdd_mmr'))")
            result['installed'] |= identity['installed']
            if not identity['in_recovery']:
                writable.append((node, identity['installed']))
        except Exception as exc:
            errors.append(node+': '+str(exc))
    if len(writable) != 1:
        result['error'] = ('检测到多个可写主库，未执行 MMR 诊断' if len(writable) > 1 else
                           '没有可确认的可写主库；未在物理备库执行订阅诊断')
        if errors:
            result['error'] += '；'+'；'.join(errors)
        return result
    node, installed = writable[0]
    inst = runtime.config.instance(node)
    result['source'] = {'instance': node, 'host': inst['host_config']['address'], 'port': inst['port']}
    result['installed'] = installed
    if not installed:
        return result
    try:
        metadata = query(node, "SELECT json_build_object('local_id',(SELECT node_id FROM fdd.mmr_local_node LIMIT 1),"
                         "'nodes',COALESCE((SELECT json_agg(json_build_object('nodeid',node_id,'nodename',node_name,"
                         "'nodestate',node_state)) FROM fdd.mmr_node),'[]'::json))")
        result['metadata'] = metadata['nodes']
        local = next((n for n in metadata['nodes'] if n['nodeid'] == metadata['local_id']), None)
        if local is None:
            result['error'] = '插件已安装，当前数据库尚未初始化 MMR 本地成员'
            return result
        result['local_member'] = local['nodename']
        if local['nodestate'] != 'ACTIVE':
            # The UDF requires an ACTIVE local node; never reinterpret metadata as real state.
            result['error'] = '本地成员为 '+local['nodestate']+'；此阶段只展示登记状态，未执行要求 ACTIVE 的诊断函数'
            return result
        result['records'] = query(node, 'SELECT CASE WHEN pg_is_in_recovery() THEN NULL ELSE '
                                  '(SELECT COALESCE(json_agg(t),\'[]\'::json) FROM ('+DIAGNOSTIC_SQL+') t) END')
        if result['records'] is None:
            result['records'] = []
            raise ValueError('采集期间节点变成物理备库，未执行 MMR 诊断；请刷新')
        if not result['records']:
            result['error'] = 'MMR 诊断未返回任何成员，不能确认实际状态'
    except Exception as exc:
        result['error'] = str(exc)
    return result


def aggregate(observations):
    members = {}
    for obs in observations:
        local = obs.get('local_member')
        for row in obs['metadata']:
            name = row['nodename']
            item = members.setdefault(name, {'name': name, 'states': [], 'registered_states': [], 'issues': [],
                                              'source': None, 'physical_group': None, 'unknown': False})
            if row['nodestate'] not in item['registered_states']:
                item['registered_states'].append(row['nodestate'])
            if name == local:
                item['local_registered_state'] = row['nodestate']
                item['source'] = obs['source']
                item['physical_group'] = obs['physical_group']
                if obs['error']:
                    item['unknown'] = True
        for row in obs['records']:
            name = row['nodename']
            item = members.setdefault(name, {'name': name, 'states': [], 'registered_states': [], 'issues': [],
                                              'source': None, 'physical_group': None, 'unknown': False})
            state = row.get('real_nodestate')
            if state and state not in {'UNKNOW', 'UNKNOWN'} and state not in item['states']:
                item['states'].append(state)
            if not state or state in {'UNKNOW', 'UNKNOWN'}:
                item['unknown'] = True
            if row.get('is_abnormal') != 'OK':
                issue = {'code': row.get('is_abnormal') or 'UNKNOWN', 'detail': row.get('detail') or '',
                         'observer': obs['source']['instance'] if obs['source'] else None}
                if issue not in item['issues']:
                    item['issues'].append(issue)
    for item in members.values():
        item['state'] = item['states'][0] if len(item['states']) == 1 and not item['unknown'] else None
        item['state_conflict'] = len(item['states']) > 1
    return sorted(members.values(), key=lambda item: item['name'])


def collect(runtime, target, database='postgres'):
    from datetime import datetime, timezone
    kind, name = target.split('.', 1)
    cluster = runtime.config.mmr_clusters.get(name) if kind == 'mmr' else None
    database = (cluster or {}).get('database') or database
    groups = {}
    names = runtime.target_instances(target)
    expected = {}
    for group, stream in runtime.config.streaming_clusters.items():
        members = [stream['primary']]+[r['instance'] for r in stream.get('standbys', [])]
        if set(members) & set(names):
            groups[group] = members
    grouped = {node for members in groups.values() for node in members}
    for node in names:
        if node not in grouped:
            groups[node] = [node]
    if cluster:
        expected = {m['streaming_cluster']: m['node_name'] for m in cluster['members'].values()}
    with ThreadPoolExecutor(max_workers=4) as pool:
        observations = list(pool.map(lambda pair: observe(runtime, pair[0], pair[1], database), groups.items()))
    members = aggregate(observations)
    for group, member in expected.items():
        obs = next((o for o in observations if o['physical_group'] == group), None)
        item = next((m for m in members if m['name'] == member), None)
        if not item:
            item = {'name': member, 'states': [], 'registered_states': [], 'state': None, 'issues': [],
                    'source': None, 'physical_group': group, 'unknown': True, 'state_conflict': False}
            members.append(item)
        item['physical_group'] = group
        if not obs or obs['local_member'] != member or obs['error'] or not obs['installed']:
            item['unknown'] = True
            item['state'] = None
    applicable = bool(cluster) or any(o['installed'] for o in observations)
    errors = [o['error'] for o in observations if o['error']]
    issue_count = len({(m['name'], i['code'], i['detail']) for m in members for i in m['issues']})
    return {'applicable': applicable, 'group_name': (cluster or {}).get('group_name'),
            'database': database, 'checked_at': datetime.now(timezone.utc).isoformat(),
            'members': sorted(members, key=lambda m:m['name']), 'observations': observations,
            'issue_count': issue_count, 'unknown': bool(errors) or any(m['unknown'] or m['state_conflict'] for m in members)}


def read(settings, environment):
    args = [sys.executable, str(Path(__file__).resolve()), '--engine-root', str(settings.pgcluster_root),
            '--config', environment['deployment_config'], '--target', environment['deployment_target'],
            '--database', environment.get('database_name') or 'postgres']
    try:
        proc = subprocess.run(args, text=True, capture_output=True, timeout=25)
    except subprocess.TimeoutExpired as exc:
        raise ValueError('MMR 状态检查超时；未使用旧结果冒充当前状态') from exc
    if proc.returncode:
        raise ValueError(proc.stderr.strip()[-1500:] or 'MMR 状态采集失败')
    return json.loads(proc.stdout)


def main():
    import argparse
    parser = argparse.ArgumentParser()
    for key in ('engine-root', 'config', 'target', 'database'):
        parser.add_argument('--'+key, required=True)
    args = parser.parse_args()
    sys.path.insert(0, args.engine_root)
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from pgclusterlib.config import load
    from pgclusterlib.runtime import Runtime
    from pgclusterlib.executor import LOCAL_HOSTS
    from platform_app.resources import local_addresses
    LOCAL_HOSTS.update(local_addresses())
    runtime = Runtime(load(args.config))
    print(json.dumps(collect(runtime, args.target, args.database), ensure_ascii=False))


if __name__ == '__main__':
    main()
