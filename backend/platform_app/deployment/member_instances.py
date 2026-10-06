"""Physical instance lifecycle belonging to reviewed replication member changes."""

import json

from .change_execution import _quote_literal
from .member_changes import new_member_streams, quote_ident, retired_member_streams


class MemberInstances:
    def __init__(self, executor):
        self.e = executor
        self.new = new_member_streams(executor.current, executor.runtime.config.raw)
        self.retired = retired_member_streams(executor.current, executor.runtime.config.raw)
        self.new_nodes = {node for group in self.new.values() for node in group['nodes']}
        self.retired_nodes = {node for group in self.retired.values() for node in group['nodes']}

    def initialize(self):
        for name, group in self.new.items():
            step_key = 'initialize-member-stream:' + name
            for node in group['nodes']:
                instance = self.e.runtime.config.instance(node)
                host = instance['host_config']['address']
                if self.e.runtime.executor.is_nonempty_dir(host, instance['data_dir']):
                    marker = self.e.runtime._marker(node)
                    if not self.e.runtime.executor.exists(host, marker):
                        raise ValueError('新增成员目录非空且无受管标记: ' + node)
                    receipt = json.loads(self.e.runtime.executor.read_text(host, marker))
                    recovering = self.e.state.get('in_progress') == step_key or step_key in self.e.state['completed']
                    if receipt.get('node') != node or (receipt.get('deployment_plan') != self.e.plan['id'] and not recovering):
                        raise ValueError('新增成员目录不属于当前部署计划: ' + node)
            cluster = self.e.runtime.config.raw[group['section']][group['cluster']]
            library = 'fdd_mmr' if group['section'] == 'mmr_clusters' else 'citus'
            preloads = (self.e.runtime.config.raw.get('postgresql_config') or {}).get('parameters', {}).get('shared_preload_libraries', [])
            if isinstance(preloads, str):
                preloads = [word.strip() for word in preloads.split(',') if word.strip()]
            extra = dict((cluster.get('postgresql_config') or {}).get('parameters', {}))
            extra.update(shared_preload_libraries=sorted(set(preloads) | {library}),
                         wal_level='logical', max_replication_slots=32, max_wal_senders=32, max_worker_processes=64)
            if library == 'fdd_mmr':
                extra.update(track_commit_timestamp='on', **{'fdd.running_databases': cluster.get('database', 'postgres')})
            def initialize(n=name, params=extra, members=group['nodes']):
                self.e.runtime.create_streaming(n, params)
                for node in members:
                    instance = self.e.runtime.config.instance(node)
                    self.e.runtime.executor.write_text(instance['host_config']['address'], self.e.runtime._marker(node), json.dumps({
                        'node': node, 'deployment_plan': self.e.plan['id'],
                    }))
            self.e.step(step_key, initialize)
            primary = self.e.runtime.config.raw['streaming_clusters'][name]['primary']
            database = cluster.get('database', 'postgres')
            def create_database(p=primary, db=database):
                if self.e.sql(self.e.runtime, p, 'SELECT count(*) FROM pg_database WHERE datname=' + _quote_literal(db)).strip() == '0':
                    self.e.sql(self.e.runtime, p, 'CREATE DATABASE ' + quote_ident(db))
            self.e.step('initialize-member-database:' + name, create_database)
            if library == 'citus':
                self.e.step('initialize-citus-extension:' + name, lambda p=primary, c=cluster: self.e.runtime._psql(
                    p, 'CREATE EXTENSION IF NOT EXISTS citus', c.get('database', 'postgres')))

    def retire(self):
        for group in self.retired.values():
            for node in reversed(group['nodes']):
                self.e.step('stop-retired-member:' + node, lambda n=node: self.e.stop(self.e.old_runtime, n))
