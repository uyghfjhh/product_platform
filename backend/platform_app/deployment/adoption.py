"""Compile observed identities into an import plan; never initialise instances."""
import ipaddress

import yaml

from .probes import probe


def local_endpoint(address, host):
    if address in ('', 'localhost', host):
        return True
    try:
        return ipaddress.ip_address(address).is_loopback
    except ValueError:
        return False


def compile_inventory(host, ssh, instances):
    if not instances:
        raise ValueError('未发现实例')
    ports = [node['port'] for node in instances]
    paths = [node['data_dir'] for node in instances]
    if len(set(ports)) != len(ports) or len(set(paths)) != len(paths):
        raise ValueError('探测到重复端口或同一实际数据目录')
    config = {'hosts': {'adopt_host': {'address': host, **({'ssh': ssh} if ssh else {})}},
              'postgresql_installations': {}, 'instances': {}, 'streaming_clusters': {}}
    homes = {}
    primaries = {}
    streams = {}
    warnings = []
    for index, node in enumerate(instances, 1):
        node['name'] = 'instance_' + str(index)
        if node.get('warning'):
            warnings.append(node['warning'])
        if node.get('mmr_configured') and not node['running']:
            raise ValueError('MMR 实例需运行并允许只读连接后才能识别成员关系: ' + node['data_dir'])
        if node['home'] not in homes:
            installation = 'installation_' + str(len(homes) + 1)
            homes[node['home']] = installation
            config['postgresql_installations'][installation] = {'provider': 'postgres', 'home': node['home']}
        config['instances'][node['name']] = {'host': 'adopt_host', 'installation': homes[node['home']],
                                           'port': node['port'], 'data_dir': node['data_dir']}
        if not node['standby']:
            if node['system_identifier'] in primaries:
                raise ValueError('同一数据库系统标识存在多个主节点，需先核对角色')
            primaries[node['system_identifier']] = node
            stream = 'stream_' + str(index)
            streams[node['name']] = stream
            config['streaming_clusters'][stream] = {'primary': node['name'], 'standbys': [], 'replication': {'mode': 'async'}}
    for node in instances:
        if not node['standby']:
            continue
        primary = primaries.get(node['system_identifier'])
        upstream = node.get('upstream') or {}
        if not primary or upstream.get('port') != primary['port'] or not local_endpoint(upstream.get('host'), host):
            raise ValueError('备库未找到唯一对应主库；请包含完整主备数据目录: ' + node['data_dir'])
        config['streaming_clusters'][streams[primary['name']]]['standbys'].append({'instance': node['name']})
    mmr_clusters = {}
    by_port = {node['port']: node for node in instances}
    covered = set()
    for node in primaries.values():
        for group in node.get('mmr_groups', []):
            uuid = group['group_uuid']
            members = [m for m in node['mmr_nodes'] if m['group_id'] == group['group_id']]
            if not members:
                raise ValueError('MMR 组没有成员')
            if any(not local_endpoint(m['endpoint']['host'], host) for m in members):
                raise ValueError('MMR 含跨主机成员；请导入完整 YAML')
            if any(m['dbname'] != 'postgres' for m in members):
                raise ValueError('自动接管当前仅支持 postgres 数据库中的 MMR；请导入 YAML')
            fingerprint = sorted((m['node_id'], m['node_name'], m['endpoint']['port']) for m in members)
            compiled = {}
            for index, member in enumerate(members, 1):
                endpoint = member['endpoint']
                primary = by_port.get(endpoint['port'])
                if not primary or not local_endpoint(endpoint['host'], host):
                    raise ValueError('MMR 含未探测到的成员，请包含完整集群；跨主机接管需导入 YAML')
                if primary['standby']:
                    primary = primaries[primary['system_identifier']]
                    warnings.append('MMR 成员 ' + member['node_name'] + ' 的登记端点指向备库；按实际系统标识关联当前主库，保留原 MMR 元数据')
                peer_group = next((g for g in primary['mmr_groups'] if g['group_uuid'] == uuid), None)
                peer_members = [m for m in primary['mmr_nodes'] if peer_group and m['group_id'] == peer_group['group_id']]
                actual = sorted((m['node_id'], m['node_name'], m['endpoint']['port']) for m in peer_members)
                if (actual != fingerprint or peer_group['group_name'] != group['group_name']
                        or any(not local_endpoint(m['endpoint']['host'], host) for m in peer_members)):
                    raise ValueError('MMR 各节点的组或成员信息不一致，不能自动接管')
                if member['node_state'] != 'ACTIVE':
                    warnings.append('MMR 成员 ' + member['node_name'] + ' 状态为 ' + member['node_state'])
                compiled['member_' + str(index)] = {'node_name': member['node_name'], 'streaming_cluster': streams[primary['name']]}
                covered.add(streams[primary['name']])
            if uuid not in mmr_clusters:
                mmr_clusters[uuid] = {'database': members[0]['dbname'], 'group_name': group['group_name'], 'extensions': ['fdd_mmr'], 'members': compiled}
    if mmr_clusters:
        config['mmr_clusters'] = {'discovered_' + str(index): group for index, group in enumerate(mmr_clusters.values(), 1)}
    # An independent development instance belongs to the managed environment,
    # but must not be represented as an MMR member or a physical standby.
    if len(mmr_clusters) == 1:
        auxiliary = [node['name'] for node in primaries.values()
                     if streams[node['name']] not in covered
                     and not config['streaming_clusters'][streams[node['name']]]['standbys']]
        if auxiliary:
            next(iter(config['mmr_clusters'].values()))['auxiliary_instances'] = auxiliary
            warnings.append('独立实例作为附属实例纳入环境管理，不加入 MMR 或物理复制关系: ' + ', '.join(auxiliary))
    targets = [{'value': 'mmr.' + name, 'label': '多活 · ' + group['group_name']} for name, group in config.get('mmr_clusters', {}).items()]
    targets += [{'value': 'streaming.' + name, 'label': '主备 · ' + group['primary']} for name, group in config['streaming_clusters'].items() if name not in covered]
    return {'source_yaml': yaml.safe_dump(config, sort_keys=False, allow_unicode=True),
            'targets': targets, 'instances': instances, 'warnings': list(dict.fromkeys(warnings))}


def discover_existing(item):
    request = item.model_dump()
    ssh = request.pop('ssh') or None
    result = probe(item.host, request, ssh=ssh, agent='adoption_agent.py')
    return compile_inventory(item.host, ssh, result['instances'])
