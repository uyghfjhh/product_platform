"""Opt-in read-only verification against the configured native Kingbase pair."""

import os

import pytest
from platform_app.studio import studio_dispatch

from products.kingbase.database import identity
from products.kingbase.provider import KingbaseProvider

pytestmark = pytest.mark.skipif(os.environ.get('KINGBASE_LIVE') != '1', reason='requires native Kingbase pair')


@pytest.mark.parametrize('port,recovery', [(54321, False), (54322, True)])
def test_native_studio_and_monitoring(port, recovery):
    environment = {'id': 'kingbase-live', 'host': '127.0.0.1', 'port': port,
                   'database_name': 'kingbase', 'database_user': 'kingbase'}
    live = identity(environment)
    assert live['recovery'] is recovery
    assert live['database_mode'] == 'oracle'
    provider = KingbaseProvider()
    assert provider.observe_runtime(None, environment)
    target = {**environment, '_studio_inspect_queries': provider.studio_inspect_queries()}
    for view in ('sessions', 'locks', 'replication', 'receiver', 'settings'):
        response = studio_dispatch(target, {'procedure': 'inspect', 'view': view})
        assert response[0] is None, (view, response)
    sample = provider.monitoring_snapshot(environment, [{'id': 'node', 'host': environment['host'], 'port': port}])
    node = sample['nodes'][0]
    assert 'error' not in node
    for name, section in node['sections'].items():
        assert section['valid'] is (name != 'wal'), (name, section)
