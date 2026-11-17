from copy import deepcopy
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from platform_app.action_catalog import actions_for_environment
from platform_app.api import create_app
from platform_app.deployment.models import DeploymentSpec
from platform_app.deployment.workbench import compile_spec
from test_api import settings_for

from products.kingbase import database, topology
from products.kingbase.provider import KingbaseProvider
from products.kingbase.router import verify_pair


def live(recovery=False):
    return {'version': 'KingbaseES V009R001C010', 'recovery': recovery,
            'data_dir': '/opt/kes/standby' if recovery else '/opt/kes/primary',
            'system_identifier': '123456', 'database_mode': 'oracle',
            'receivers': ([{'status': 'streaming', 'sender_host': '/tmp', 'sender_port': 54321,
                            'slot_name': 'standby'}] if recovery else []),
            'senders': ([] if recovery else [{'state': 'streaming', 'sync_state': 'async'}])}


def test_register_native_pair_persists_all_resource_identities(tmp_path, monkeypatch):
    monkeypatch.setattr(database, 'identity', lambda env, node=None: live(bool(node)))
    settings = settings_for(tmp_path)
    client = TestClient(create_app(settings, enqueuer=lambda _: None))
    payload = {'id': 'kes', 'title': '金仓主备', 'host': '127.0.0.1', 'port': 54321, 'standby_port': 54322}
    response = client.post('/api/v1/products/kingbase/environments', json=payload)
    assert response.status_code == 201, response.text
    environment = response.json()
    config = yaml.safe_load(Path(environment['deployment_config']).read_text())
    assert config['instances']['standby']['data_dir'] == '/opt/kes/standby'
    assert environment['database_name'] == environment['database_user'] == 'kingbase'
    from platform_app.resources import resource_keys
    assert {'endpoint:local:54321', 'endpoint:local:54322'} <= resource_keys(environment)
    assert client.post('/api/v1/products/kingbase/environments', json=payload).status_code == 409
    conflict = client.post('/api/v1/products/kingbase/environments', json={**payload, 'id': 'duplicate'})
    assert conflict.status_code == 409
    assert not (settings.profile_dir('duplicate') / 'kingbase.yaml').exists()
    actions = client.get('/api/v1/environments/kes/actions').json()
    assert {a['id'] for a in actions} == {'database.check', 'deployment.start', 'deployment.stop', 'deployment.restart', 'deployment.doctor'}


@pytest.mark.parametrize('change,message', [
    ({'system_identifier': 'different'}, '系统标识'),
    ({'recovery': False}, '实际指向主库'),
    ({'receivers': []}, 'streaming'),
    ({'receivers': [{'status': 'streaming', 'sender_port': 65432, 'sender_host': '/tmp'}]}, '上游'),
])
def test_adoption_rejects_unrelated_or_invalid_standby(change, message):
    standby = {**live(True), **change}
    with pytest.raises(ValueError, match=message):
        verify_pair({'host': '127.0.0.1', 'port': 54321}, live(),
                    {'host': '127.0.0.1', 'port': 54322}, standby)


def test_socket_upstream_does_not_identify_primary_on_different_host():
    with pytest.raises(ValueError, match='上游'):
        verify_pair({'host': '10.0.0.1', 'port': 54321}, live(),
                    {'host': '10.0.0.2', 'port': 54322}, live(True))


def test_role_observation_and_failure_are_not_fabricated(tmp_path, monkeypatch):
    environment = {'host': '127.0.0.1', 'port': 54321}
    monkeypatch.setattr(topology, 'identity', lambda *args: live(True))
    value = topology.topology(None, environment)
    assert value['nodes'][0]['role'] == 'standby'
    assert topology.status(None, environment)['primary']['running'] is True
    monkeypatch.setattr(topology, 'identity', lambda *args: (_ for _ in ()).throw(ConnectionError()))
    assert topology.status(None, environment)['primary']['running'] is None


def test_kingbase_does_not_enter_postgres_deployment_engine(tmp_path):
    spec = DeploymentSpec(title='KES', product_id='kingbase', template_id='ha',
                          mode='import', host='127.0.0.1', source_yaml='{}', target='streaming.cluster')
    with pytest.raises(ValueError, match='尚未提供部署'):
        compile_spec(settings_for(tmp_path), spec, 'kes')
    assert not any(a['id'] in ('deployment.clean', 'deployment.create', 'deployment.failover') for a in actions_for_environment({'product_id': 'kingbase'}, settings_for(tmp_path)))


def test_studio_receiver_query_is_product_specific():
    queries = KingbaseProvider().studio_inspect_queries()
    assert 'received_lsn' in queries['receiver']
    assert 'written_lsn' not in queries['receiver']
    from platform_app.studio_operations import QUERIES
    assert 'written_lsn' in QUERIES['receiver']


def test_physical_link_requires_native_identity_route_and_slot_evidence():
    from products.kingbase.monitoring import links
    def node(name, port, recovery):
        return {'node': {'id': name, 'host': '127.0.0.1', 'port': port}, 'sections': {
            'runtime': {'valid': True, 'rows': [{'recovery': recovery}]},
            'system': {'valid': True, 'rows': [{'system_identifier': '123'}]},
            'receivers': {'valid': True, 'rows': []},
            'senders': {'valid': True, 'rows': []},
        }}
    source, target = node('p', 54321, False), node('s', 54322, True)
    source['sections']['senders']['rows'] = [{'slot_name': 'physical_s', 'state': 'streaming', 'pid': 123}]
    target['sections']['receivers']['rows'] = [{'status': 'streaming', 'slot_name': 'physical_s', 'sender_host': '/tmp', 'sender_port': 54321}]
    configured = {'edges': [{'id': 'physical:p:s', 'source': 'p', 'target': 's'}]}
    assert links({'nodes': [source, target]}, configured)['links'][0]['status'] == 'connected'
    target['sections']['system']['rows'][0]['system_identifier'] = 'different'
    assert links({'nodes': [source, target]}, configured)['links'][0]['status'] == 'unknown'
    target['sections']['system']['rows'][0]['system_identifier'] = '123'
    target['sections']['receivers']['rows'][0]['sender_port'] = 54323
    assert links({'nodes': [source, target]}, configured)['links'][0]['status'] == 'unknown'


def test_registered_pair_routes_topology_and_studio_to_native_provider(tmp_path, monkeypatch):
    monkeypatch.setattr(database, 'identity', lambda env, node=None: live(bool(node)))
    monkeypatch.setattr(topology, 'identity', lambda env, node=None: live(node['id'] == 'standby'))
    client = TestClient(create_app(settings_for(tmp_path), enqueuer=lambda _: None))
    assert client.post('/api/v1/products/kingbase/environments', json={
        'id': 'kes', 'title': 'KES', 'standby_port': 54322}).status_code == 201
    graph = client.get('/api/v1/environments/kes/topology').json()
    assert [n['role'] for n in graph['nodes']] == ['primary', 'standby']
    assert graph['edges'][0]['source'] == 'primary'
    seen = []
    monkeypatch.setattr('platform_app.api.routes_environments.studio_dispatch',
                        lambda env, body: seen.append(deepcopy(env)) or [None, []])
    assert client.post('/api/v1/environments/kes/studio/nodes/standby', json={'procedure': 'inspect', 'view': 'receiver'}).json() == [None, []]
    assert seen[0]['port'] == 54322
    assert 'received_lsn' in seen[0]['_studio_inspect_queries']['receiver']
    assert client.post('/api/v1/environments/kes/studio/nodes/unknown', json={'procedure': 'query'}).status_code == 404
