"""Product-owned Kingbase dialect and existing-cluster observations."""

from platform_app.observations import ParsedObservation
from platform_app.providers import Observation

from products.kingbase import database
from products.kingbase import topology as native_topology


class KingbaseProvider:
    def lifecycle_command(self, settings, environment, action, target, parameters):
        import json
        import sys
        from pathlib import Path
        from platform_app.providers import CommandSpec
        from products.kingbase.topology import inventory
        if action not in {'deployment.start', 'deployment.stop', 'deployment.restart', 'deployment.doctor'}:
            raise ValueError('金仓未提供此操作')
        nodes = inventory(environment)
        if target != environment.get('deployment_target'):
            nodes = [n for n in nodes if n['id'] == target]
        if not nodes:
            raise ValueError('金仓操作目标不存在')
        return CommandSpec([sys.executable, str(Path(__file__).with_name('lifecycle.py')),
                            action.split('.')[1], json.dumps(nodes)], settings.products_root.parent)

    # PostgreSQL pgbench/JDBC defaults are not validated for Kingbase Oracle mode.
    workload_actions = ()

    def discover(self, settings):
        return []

    def validate_target(self, settings, target):
        return target.startswith('streaming.')

    def command(self, settings, environment, action, target, parameters):
        raise ValueError('金仓当前支持现有环境管理，尚未提供部署生命周期操作')

    def observe_database(self, environment):
        live = database.identity(environment)
        return [Observation('sql.version', 'ready', {'version': live['version'],
                'database_mode': live['database_mode'], 'recovery': live['recovery']})]

    def observe_runtime(self, settings, environment):
        live = database.identity(environment)
        return [ParsedObservation('kingbase.replication', row['state'],
                                  {'entity_id': row['application_name'], **row})
                for row in live['senders']] + [
                    ParsedObservation('kingbase.receiver', row['status'],
                                      {'entity_id': row['slot_name'], **row})
                    for row in live['receivers']]

    topology = staticmethod(native_topology.topology)
    status = staticmethod(native_topology.status)
    studio_inspect_queries = staticmethod(database.inspect_queries)
    validate_environment = staticmethod(native_topology.inventory)

    def monitoring_snapshot(self, environment, nodes):
        from products.kingbase.monitoring import snapshot
        return snapshot(environment, nodes)

    def monitoring_links(self, current, configured, previous=None):
        from products.kingbase.monitoring import links
        return links(current, configured, previous)


PROVIDER = KingbaseProvider()
