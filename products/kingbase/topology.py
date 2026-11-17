"""Existing Kingbase cluster inventory; SQL-observed roles never imply ownership."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import psycopg
import yaml
from platform_app.resources import canonical_host

from products.kingbase.database import identity


def local_server_pid(node):
    if canonical_host(node['host']) != 'local' or not node.get('data_dir'):
        return None
    for entry in Path('/proc').iterdir():
        if not entry.name.isdigit():
            continue
        try:
            argv = (entry / 'cmdline').read_bytes().split(b'\0')
            if Path(argv[0].decode()).name != 'kingbase':
                continue
            for index, arg in enumerate(argv[:-1]):
                if arg == b'-D' and argv[index + 1].decode() == node['data_dir']:
                    return int(entry.name)
        except (OSError, UnicodeError):
            continue
    return None


def inventory(environment):
    try:
        return _inventory(environment)
    except (KeyError, TypeError, yaml.YAMLError) as exc:
        raise ValueError('金仓库存配置结构无效') from exc


def _inventory(environment):
    path = environment.get('deployment_config')
    if not path:
        return [{'id': 'primary', 'label': '金仓实例', 'host': environment['host'],
                 'port': environment['port'], 'data_dir': '', 'role': 'instance', 'group': 'kingbase'}]
    config = yaml.safe_load(Path(path).read_text())
    if not isinstance(config, dict) or config.get('product_id') != 'kingbase':
        raise ValueError('需要金仓现有环境配置')
    target = environment.get('deployment_target') or ''
    kind, _, name = target.partition('.')
    cluster = (config.get('streaming_clusters') or {}).get(name)
    if kind != 'streaming' or not cluster:
        raise ValueError('金仓部署目标不在配置中')
    primary = cluster['primary']
    names = [primary] + [item['instance'] for item in cluster.get('standbys', [])]
    if len(names) != len(set(names)):
        raise ValueError('金仓实例不能重复')
    nodes = []
    for node_id in names:
        node = config['instances'][node_id]
        host = config['hosts'][node['host']]['address']
        port = int(node['port'])
        if not 1 <= port <= 65535:
            raise ValueError('金仓实例端口无效')
        data_dir = Path(node['data_dir'])
        if not data_dir.is_absolute() or data_dir == Path('/') or '..' in data_dir.parts:
            raise ValueError('金仓实例需要非根绝对数据目录')
        nodes.append({'id': node_id, 'label': node_id, 'host': host, 'port': port,
                      'data_dir': node['data_dir'], 'group': name,
                      'wal_directory': str(Path(node['data_dir']) / 'sys_wal'),
                      'role': 'primary' if node_id == primary else 'standby'})
    if (nodes[0]['host'], nodes[0]['port']) != (environment['host'], environment['port']):
        raise ValueError('环境连接与金仓配置主节点不一致，请重新登记环境')
    return nodes


def observations(environment, nodes):
    def observe(node):
        try:
            live = identity(environment, node)
            if node['data_dir'] and live['data_dir'] != node['data_dir']:
                raise ValueError('实际数据目录与登记不一致')
            return {'live': live}
        except (psycopg.Error, ValueError, OSError) as exc:
            return {'error': '实例连接或身份检查失败：' + (str(exc) if isinstance(exc, ValueError) else type(exc).__name__)}
    with ThreadPoolExecutor(max_workers=4) as pool:
        return list(pool.map(observe, nodes))


def topology(settings, environment):
    nodes = inventory(environment)
    live_nodes = observations(environment, nodes)
    for node, observed in zip(nodes, live_nodes):
        node['declared_role'] = node['role']
        node['installed_extensions'] = []
        node['extension_versions'] = {}
        live = observed.get('live')
        if live:
            node.update(role='standby' if live['recovery'] else 'primary',
                        server_version=live['version'], database_version=live['version'],
                        database_mode=live['database_mode'])
        else:
            node['observation_error'] = observed['error']
    primary = next((n for n in nodes if n['declared_role'] == 'primary'), None)
    edges = [{'id': 'physical:' + primary['id'] + ':' + n['id'], 'source': primary['id'],
              'target': n['id'], 'kind': 'streaming'} for n in nodes if primary and n['id'] != primary['id']]
    return {'target': environment.get('deployment_target') or 'streaming.kingbase',
            'kind': 'streaming', 'nodes': nodes, 'edges': edges}


def status(settings, environment):
    nodes = inventory(environment)
    observed = observations(environment, nodes)
    result = {}
    for node, value in zip(nodes, observed):
        live = value.get('live')
        result[node['id']] = {'running': True if live else None, 'pid': local_server_pid(node) if live else None,
            'message': ('KingbaseES · ' + ('备库' if live['recovery'] else '主库') +
                        ' · ' + live['database_mode']) if live else value['error']}
    return result
