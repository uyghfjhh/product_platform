"""Opt-in real database acceptance; every PGDATA is created under pytest's tmpdir.

PLATFORM_TEST_POSTGRES_HOME=/path/to/postgres pytest tests/test_deployment_live.py
Optional: PLATFORM_TEST_MMR_HOME / PLATFORM_TEST_CITUS_HOME for plugin acceptance.
No registered environments or external databases are used.
"""

import json
import os
import secrets
import socket
import subprocess
import threading
import time
from copy import deepcopy
from pathlib import Path

import pytest
import yaml
from platform_app.config import load_settings
from platform_app.deployment.change_execution import ChangeExecutor
from platform_app.deployment.workbench import diff_operations


class DatabaseLab:
    def __init__(self, root, home):
        self.root, self.home = root, Path(home)
        self.directories = []
        self.raw = {'hosts': {'local': {'address': '127.0.0.1'}},
                    'postgresql_installations': {'pg': {'provider': 'postgres', 'home': str(self.home)}},
                    'instances': {}, 'streaming_clusters': {}}

    def command(self, args):
        return subprocess.run([str(arg) for arg in args], check=True, capture_output=True,
                              text=True, timeout=90).stdout.strip()

    def port(self):
        for _ in range(100):
            value = 18000 + secrets.randbelow(7000)  # below Linux client ephemeral ports
            if any(row['port'] == value for row in self.raw['instances'].values()):
                continue
            with socket.socket() as sock:
                try:
                    sock.bind(('127.0.0.1', value))
                    return value
                except OSError:
                    continue
        raise RuntimeError('No isolated test port available')

    def database(self, name, plugin=None, source=None):
        directory = self.root/name
        self.directories.append(directory)
        port = self.port()
        if source:
            self.command([self.home/'bin/pg_basebackup', '-h', '127.0.0.1', '-p', self.raw['instances'][source]['port'],
                          '-U', 'postgres', '-D', directory, '-R', '--checkpoint=fast', '-X', 'stream', '-C', '-S', 'lab_'+name])
        else:
            self.command([self.home/'bin/initdb', '-D', directory, '-A', 'trust', '--no-locale'])
        with (directory/'postgresql.conf').open('a') as handle:
            handle.write(f"\nport={port}\nlisten_addresses='127.0.0.1'\nwal_level=logical\nmax_replication_slots=32\nmax_wal_senders=32\nmax_worker_processes=64\n")
            if plugin:
                handle.write(f"shared_preload_libraries='{plugin}'\n")
            if plugin == 'fdd_mmr':
                handle.write("fdd.running_databases='postgres'\ntrack_commit_timestamp=on\n")
        self.command([self.home/'bin/pg_ctl', 'start', '-D', directory, '-l', self.root/(name+'.log'), '-w'])
        self.raw['instances'][name] = {'host': 'local', 'installation': 'pg', 'port': port, 'data_dir': str(directory)}
        if source:
            self.raw['streaming_clusters'][source]['standbys'].append({'instance': name, 'slot': 'lab_'+name})
        else:
            self.raw['streaming_clusters'][name] = {'primary': name, 'standbys': []}
        return port

    def declare(self, name, standby=None):
        directory = self.root/name
        self.directories.append(directory)
        self.raw['instances'][name] = {'host': 'local', 'installation': 'pg', 'port': self.port(), 'data_dir': str(directory)}
        if standby:
            self.raw['streaming_clusters'][standby]['standbys'].append({'instance': name, 'slot': 'lab_'+name})
        else:
            self.raw['streaming_clusters'][name] = {'primary': name, 'standbys': []}

    def sql(self, name, statement, raw=None):
        port = (raw or self.raw)['instances'][name]['port']
        return self.command([self.home/'bin/psql', '-X', '-At', '-v', 'ON_ERROR_STOP=1', '-h', '127.0.0.1',
                             '-p', port, '-d', 'postgres', '-c', statement])

    def wait(self, predicate):
        deadline = time.monotonic() + 30
        while not predicate():
            if time.monotonic() >= deadline:
                raise AssertionError('real database state did not converge')
            time.sleep(.2)

    def executor(self, before, after, identity, target):
        from pgclusterlib.config import load
        from pgclusterlib.runtime import Runtime
        old, new = self.root/(identity+'-before.yaml'), self.root/(identity+'-after.yaml')
        old.write_text(yaml.safe_dump(before)); new.write_text(yaml.safe_dump(after))
        diff, operations, executable = diff_operations(before, after)
        assert executable, operations
        return ChangeExecutor({'id': identity, 'target': target, 'diff': diff}, before,
                              Runtime(load(new)), Runtime(load(old)), self.root/(identity+'.json'))

    def close(self):
        for directory in reversed(self.directories):
            subprocess.run([self.home/'bin/pg_ctl', 'stop', '-D', directory, '-m', 'fast', '-w', '-t', '15'],
                           capture_output=True, timeout=20)


@pytest.fixture
def lab(request, tmp_path, monkeypatch):
    variable = request.param
    home = os.environ.get(variable)
    if not home:
        pytest.skip('Set '+variable+' to run isolated real database acceptance')
    if not (Path(home)/'bin/initdb').is_file():
        pytest.fail('Configured test installation has no initdb')
    monkeypatch.syspath_prepend(str(load_settings().pgcluster_root))
    # The libraries come from the selected installation, not a different
    # database binary on the operator's interactive shell path.
    monkeypatch.setenv('LD_LIBRARY_PATH', str(Path(home)/'lib'))
    value = DatabaseLab(tmp_path, home)
    try:
        yield value
    finally:
        value.close()


@pytest.mark.parametrize('lab', ['PLATFORM_TEST_POSTGRES_HOME'], indirect=True)
def test_primary_migration_retains_committed_writes_and_resumes_after_promotion(lab):
    import psycopg
    source_port = lab.database('p')
    lab.sql('p', 'CREATE TABLE evidence(id int PRIMARY KEY)')
    lab.database('s', source='p')
    before = deepcopy(lab.raw); desired = deepcopy(before)
    new_directory = lab.root/'new-primary'; lab.directories.append(new_directory)
    desired['instances']['p'].update(port=lab.port(), data_dir=str(new_directory))
    executor = lab.executor(before, desired, 'primary', 'streaming.p')
    accepted = set()
    def writer():
        try:
            with psycopg.connect(host='127.0.0.1', port=source_port, user='postgres', dbname='postgres', autocommit=True) as connection:
                for number in range(10000):
                    connection.execute('INSERT INTO evidence VALUES (%s)', (number,))
                    accepted.add(number)
                    time.sleep(.002)
        except psycopg.Error:
            pass  # normal source fencing disconnects this test client
    thread = threading.Thread(target=writer, daemon=True); thread.start()
    promote = executor.runtime._promote
    def interrupted(node):
        promote(node)
        raise RuntimeError('test interruption after actual promotion')
    executor.runtime._promote = interrupted
    try:
        with pytest.raises(RuntimeError, match='actual promotion'):
            executor.run()
    finally:
        thread.join(10)
    assert accepted and not thread.is_alive()
    executor = lab.executor(before, desired, 'primary', 'streaming.p')
    executor.run()
    rows = {int(row) for row in lab.sql('p', 'SELECT id FROM evidence', desired).splitlines()}
    assert accepted <= rows
    assert (lab.root/'p'/'PG_VERSION').is_file()
    assert not executor.old_runtime.status_instance('p')['running']
    lab.sql('p', 'INSERT INTO evidence VALUES (10001)', desired)
    lab.wait(lambda: lab.sql('s', 'SELECT count(*) FROM evidence WHERE id=10001') == '1')
    assert json.loads(executor.path.read_text())['status'] == 'APPLIED'


@pytest.mark.parametrize('lab', ['PLATFORM_TEST_POSTGRES_HOME'], indirect=True)
def test_logical_link_add_replace_retire_preserves_old_subscriber(lab):
    for node in ('p', 's', 't'):
        lab.database(node); lab.sql(node, 'CREATE TABLE evidence(id int PRIMARY KEY)')
    lab.sql('p', 'INSERT INTO evidence VALUES (1)')
    before = deepcopy(lab.raw); desired = deepcopy(before)
    desired['logical_replications'] = {'link': {'pub': {'streaming_cluster': 'p'}, 'sub': {'streaming_cluster': 's'}}}
    lab.executor(before, desired, 'logical-add', 'streaming.p').run()
    lab.wait(lambda: lab.sql('s', 'SELECT count(*) FROM evidence') == '1')
    changed = deepcopy(desired); changed['logical_replications']['link']['sub']['streaming_cluster'] = 't'
    lab.executor(desired, changed, 'logical-change', 'streaming.p').run()
    lab.sql('p', 'INSERT INTO evidence VALUES (2)')
    lab.wait(lambda: lab.sql('t', 'SELECT count(*) FROM evidence') == '2')
    assert lab.sql('s', 'SELECT count(*) FROM evidence') == '1'
    lab.executor(changed, before, 'logical-retire', 'streaming.p').run()
    assert lab.sql('t', 'SELECT count(*) FROM evidence') == '2'
    assert lab.sql('p', "SELECT count(*) FROM pg_replication_slots WHERE slot_name='link_slot'") == '0'


@pytest.mark.parametrize('lab', ['PLATFORM_TEST_MMR_HOME'], indirect=True)
def test_mmr_member_add_and_retire_preserves_business_tables(lab):
    for node in ('a', 'b'):
        port = lab.database(node, 'fdd_mmr')
        lab.sql(node, 'CREATE EXTENSION fdd_mmr')
        lab.sql(node, "SELECT fdd.create_node('%s','host=127.0.0.1 port=%s dbname=postgres user=postgres')" % (node, port))
    lab.sql('a', "SELECT fdd.create_group('lab_group')")
    lab.sql('a', 'CREATE TABLE evidence(id int PRIMARY KEY); INSERT INTO evidence VALUES(1)')
    lab.sql('b', "SELECT fdd.join_group('lab_group','host=127.0.0.1 port=%s dbname=postgres user=postgres',true,'all','table_exist_error')" % lab.raw['instances']['a']['port'])
    before = deepcopy(lab.raw)
    before['mmr_clusters'] = {'cluster': {'group_name': 'lab_group', 'database': 'postgres', 'extensions': ['fdd_mmr'],
                                        'members': {n: {'node_name': n, 'streaming_cluster': n} for n in ('a', 'b')}}}
    lab.declare('c'); lab.declare('d', standby='c')
    desired = deepcopy(before)
    desired['instances'] = deepcopy(lab.raw['instances']); desired['streaming_clusters'] = deepcopy(lab.raw['streaming_clusters'])
    desired['mmr_clusters']['cluster']['members']['c'] = {'node_name': 'c', 'streaming_cluster': 'c'}
    lab.executor(before, desired, 'mmr-add', 'mmr.cluster').run()
    lab.wait(lambda: lab.sql('c', 'SELECT count(*) FROM evidence') == '1')
    lab.wait(lambda: lab.sql('d', 'SELECT count(*) FROM evidence') == '1')
    lab.sql('c', 'INSERT INTO evidence VALUES (2)')
    lab.wait(lambda: lab.sql('a', 'SELECT count(*) FROM evidence') == '2' and lab.sql('b', 'SELECT count(*) FROM evidence') == '2')
    retired = deepcopy(desired); del retired['mmr_clusters']['cluster']['members']['b']
    del retired['streaming_clusters']['b']; del retired['instances']['b']
    execution = lab.executor(desired, retired, 'mmr-retire', 'mmr.cluster')
    execution.run()
    assert lab.sql('a', "SELECT count(*) FROM fdd.mmr_node WHERE node_name='b'") == '0'
    assert not execution.old_runtime.status_instance('b')['running']
    execution.old_runtime.start_instance('b')  # verify retained data in our isolated retired instance
    assert lab.sql('b', 'SELECT count(*) FROM evidence') == '2'


@pytest.mark.parametrize('lab', ['PLATFORM_TEST_CITUS_HOME'], indirect=True)
def test_citus_worker_drain_moves_real_shards_before_retirement(lab):
    for node in ('a', 'b'):
        lab.database(node, 'citus'); lab.sql(node, 'CREATE EXTENSION citus')
    coordinator = lab.raw['instances']['a']['port']; worker = lab.raw['instances']['b']['port']
    lab.sql('a', "SELECT citus_set_coordinator_host('127.0.0.1',%s)" % coordinator)
    lab.sql('a', "SELECT citus_add_node('127.0.0.1',%s)" % worker)
    lab.sql('a', 'CREATE TABLE evidence(id int PRIMARY KEY)')
    lab.sql('a', "SET citus.shard_count=4; SELECT create_distributed_table('evidence','id')")
    lab.sql('a', 'INSERT INTO evidence SELECT generate_series(1,100)')
    before = deepcopy(lab.raw)
    before['citus_clusters'] = {'cluster': {'database': 'postgres', 'extensions': ['citus'],
                                          'coordinator': {'streaming_cluster': 'a'}, 'workers': {'b': {'streaming_cluster': 'b'}}}}
    lab.declare('c'); lab.declare('d', standby='c')
    desired = deepcopy(before)
    desired['instances'] = deepcopy(lab.raw['instances']); desired['streaming_clusters'] = deepcopy(lab.raw['streaming_clusters'])
    desired['citus_clusters']['cluster']['workers']['c'] = {'streaming_cluster': 'c'}
    lab.executor(before, desired, 'citus-add', 'citus.cluster').run()
    assert lab.sql('d', 'SELECT pg_is_in_recovery()') == 't'
    retired = deepcopy(desired); del retired['citus_clusters']['cluster']['workers']['b']
    del retired['streaming_clusters']['b']; del retired['instances']['b']
    execution = lab.executor(desired, retired, 'citus-retire', 'citus.cluster')
    execution.run()
    assert lab.sql('a', 'SELECT count(*) FROM evidence') == '100'
    assert lab.sql('a', 'SELECT count(*) FROM pg_dist_node WHERE nodeport=%s' % worker) == '0'
    assert (lab.root/'b'/'PG_VERSION').is_file()
    assert not execution.old_runtime.status_instance('b')['running']


@pytest.mark.parametrize('lab', ['PLATFORM_TEST_POSTGRES_HOME'], indirect=True)
def test_logical_subscriber_primary_move_and_publisher_port_preserve_replication(lab):
    from platform_app.deployment.change_execution import _quote_literal
    from psycopg.conninfo import conninfo_to_dict, make_conninfo
    lab.database('p'); lab.database('s'); lab.database('r', source='s')
    for node in ('p', 's'):
        lab.sql(node, 'CREATE TABLE evidence(id int PRIMARY KEY)')
    before = deepcopy(lab.raw); linked = deepcopy(before)
    linked['logical_replications'] = {'link': {'pub': {'streaming_cluster': 'p'}, 'sub': {'streaming_cluster': 's'}}}
    lab.executor(before, linked, 'link-prepare', 'logical.link').run()
    conn = make_conninfo(host='127.0.0.1', port=before['instances']['p']['port'], dbname='postgres', user='postgres',
                        password='isolated-test-password', sslmode='disable', connect_timeout=5)
    lab.sql('s', 'ALTER SUBSCRIPTION link_sub CONNECTION '+_quote_literal(conn))
    lab.sql('p', 'INSERT INTO evidence VALUES (1)')
    lab.wait(lambda: lab.sql('s', 'SELECT count(*) FROM evidence') == '1')
    moved = deepcopy(linked); directory = lab.root/'new-subscriber'; lab.directories.append(directory)
    moved['instances']['s'].update(port=lab.port(), data_dir=str(directory))
    lab.executor(linked, moved, 'subscriber-primary', 'logical.link').run()
    lab.sql('p', 'INSERT INTO evidence VALUES (2)')
    lab.wait(lambda: lab.sql('s', 'SELECT count(*) FROM evidence', moved) == '2')
    lab.wait(lambda: lab.sql('r', 'SELECT count(*) FROM evidence') == '2')
    final = deepcopy(moved); final['instances']['p']['port'] = lab.port()
    lab.executor(moved, final, 'publisher-port', 'logical.link').run()
    lab.sql('p', 'INSERT INTO evidence VALUES (3)', final)
    lab.wait(lambda: lab.sql('s', 'SELECT count(*) FROM evidence', final) == '3')
    actual = conninfo_to_dict(lab.sql('s', "SELECT subconninfo FROM pg_subscription WHERE subname='link_sub'", final))
    assert actual['password'] == 'isolated-test-password' and actual['sslmode'] == 'disable'
    assert int(actual['port']) == final['instances']['p']['port']
    assert 'isolated-test-password' not in (lab.root/'publisher-port.json').read_text()


@pytest.mark.parametrize('lab', ['PLATFORM_TEST_MMR_HOME'], indirect=True)
def test_mmr_primary_port_updates_peer_subscriptions_without_losing_slots(lab):
    for node in ('a', 'b'):
        port = lab.database(node, 'fdd_mmr'); lab.sql(node, 'CREATE EXTENSION fdd_mmr')
        lab.sql(node, "SELECT fdd.create_node('%s','host=127.0.0.1 port=%s dbname=postgres user=postgres')" % (node, port))
    lab.sql('a', "SELECT fdd.create_group('port_group')")
    lab.sql('a', 'CREATE TABLE evidence(id int PRIMARY KEY); INSERT INTO evidence VALUES(1)')
    lab.sql('b', "SELECT fdd.join_group('port_group','host=127.0.0.1 port=%s dbname=postgres user=postgres',true,'all','table_exist_error')" % lab.raw['instances']['a']['port'])
    lab.database('s', source='a')
    before = deepcopy(lab.raw)
    before['mmr_clusters'] = {'cluster': {'group_name': 'port_group', 'database': 'postgres', 'extensions': ['fdd_mmr'],
                                        'members': {n: {'node_name': n, 'streaming_cluster': n} for n in ('a', 'b')}}}
    desired = deepcopy(before); desired['instances']['a']['port'] = lab.port()
    executor = lab.executor(before, desired, 'mmr-primary-port', 'mmr.cluster')
    executor.run()
    lab.sql('a', 'INSERT INTO evidence VALUES (2)', desired)
    lab.wait(lambda: lab.sql('b', 'SELECT count(*) FROM evidence') == '2')
    lab.sql('b', 'INSERT INTO evidence VALUES (3)')
    lab.wait(lambda: lab.sql('a', 'SELECT count(*) FROM evidence', desired) == '3')
    lab.wait(lambda: lab.sql('s', 'SELECT count(*) FROM evidence') == '3')
    lab.executor(before, desired, 'mmr-primary-port', 'mmr.cluster').run()
    # Full relocation must retain actual failover slots, not just table data.
    moved = deepcopy(desired); directory = lab.root/'new-mmr-primary'; lab.directories.append(directory)
    moved['instances']['a'].update(port=lab.port(), data_dir=str(directory))
    lab.executor(desired, moved, 'mmr-primary-full', 'mmr.cluster').run()
    lab.sql('a', 'INSERT INTO evidence VALUES(4)', moved)
    lab.wait(lambda: lab.sql('b', 'SELECT count(*) FROM evidence') == '4')
    lab.sql('b', 'INSERT INTO evidence VALUES(5)')
    lab.wait(lambda: lab.sql('a', 'SELECT count(*) FROM evidence', moved) == '5')
    lab.wait(lambda: lab.sql('s', 'SELECT count(*) FROM evidence') == '5')
    lab.sql('a', 'UPDATE evidence SET id=20 WHERE id=2', moved)
    lab.wait(lambda: lab.sql('b', 'SELECT count(*) FROM evidence WHERE id=20') == '1')
    lab.sql('b', 'DELETE FROM evidence WHERE id=1')
    lab.wait(lambda: lab.sql('a', 'SELECT count(*) FROM evidence', moved) == '4')
    lab.wait(lambda: lab.sql('s', 'SELECT count(*) FROM evidence') == '4')


@pytest.mark.parametrize('lab', ['PLATFORM_TEST_CITUS_HOME'], indirect=True)
def test_citus_worker_and_coordinator_primary_moves_preserve_node_identity_and_shards(lab):
    for node in ('a', 'b'):
        lab.database(node, 'citus'); lab.sql(node, 'CREATE EXTENSION citus')
    lab.database('s', source='b')
    lab.sql('a', "SELECT citus_set_coordinator_host('127.0.0.1',%s)" % lab.raw['instances']['a']['port'])
    lab.sql('a', "SELECT citus_add_node('127.0.0.1',%s)" % lab.raw['instances']['b']['port'])
    lab.sql('a', 'CREATE TABLE evidence(id int PRIMARY KEY)')
    lab.sql('a', "SET citus.shard_count=4; SELECT create_distributed_table('evidence','id')")
    lab.sql('a', 'INSERT INTO evidence SELECT generate_series(1,100)')
    before = deepcopy(lab.raw)
    before['citus_clusters'] = {'cluster': {'database': 'postgres', 'extensions': ['citus'],
                                          'coordinator': {'streaming_cluster': 'a'}, 'workers': {'b': {'streaming_cluster': 'b'}}}}
    nodeid = lab.sql('a', 'SELECT nodeid FROM pg_dist_node WHERE nodeport=%s' % before['instances']['b']['port'])
    desired = deepcopy(before); directory = lab.root/'moved-worker'; lab.directories.append(directory)
    desired['instances']['b'].update(port=lab.port(), data_dir=str(directory))
    lab.executor(before, desired, 'worker-primary', 'citus.cluster').run()
    assert lab.sql('a', 'SELECT count(*) FROM evidence') == '100'
    assert lab.sql('a', 'SELECT nodeid FROM pg_dist_node WHERE nodeport=%s' % desired['instances']['b']['port']) == nodeid
    lab.sql('a', 'INSERT INTO evidence VALUES(101)')
    assert lab.sql('a', 'SELECT count(*) FROM evidence') == '101'
    final = deepcopy(desired); directory = lab.root/'moved-coordinator'; lab.directories.append(directory)
    final['instances']['a'].update(port=lab.port(), data_dir=str(directory))
    lab.executor(desired, final, 'coordinator-primary', 'citus.cluster').run()
    assert lab.sql('a', 'SELECT count(*) FROM evidence', final) == '101'
    lab.sql('a', 'INSERT INTO evidence VALUES(102)', final)
    assert lab.sql('a', 'SELECT count(*) FROM evidence', final) == '102'


@pytest.mark.parametrize('lab', ['PLATFORM_TEST_MMR_HOME'], indirect=True)
def test_native_failover_logical_publisher_move_preserves_consumption_position(lab):
    lab.database('p'); lab.database('s'); lab.database('r', source='p')
    for node in ('p','s'):
        lab.sql(node, 'CREATE TABLE evidence(id int PRIMARY KEY)')
    lab.sql('p', 'INSERT INTO evidence VALUES(1)')
    before = deepcopy(lab.raw); linked = deepcopy(before)
    linked['logical_replications'] = {'link': {'pub': {'streaming_cluster': 'p'},
                                             'sub': {'streaming_cluster': 's', 'slot': {'failover': True}}}}
    lab.executor(before, linked, 'failover-link', 'logical.link').run()
    lab.wait(lambda: lab.sql('s', 'SELECT count(*) FROM evidence') == '1')
    lab.sql('p', 'INSERT INTO evidence SELECT generate_series(2,100)')
    lab.wait(lambda: lab.sql('s', 'SELECT count(*) FROM evidence') == '100')
    desired = deepcopy(linked); directory = lab.root/'moved-publisher'; lab.directories.append(directory)
    desired['instances']['p'].update(port=lab.port(), data_dir=str(directory))
    lab.executor(linked, desired, 'failover-publisher', 'logical.link').run()
    lab.sql('p', 'INSERT INTO evidence VALUES (101)', desired)
    lab.wait(lambda: lab.sql('s', 'SELECT count(*) FROM evidence') == '101')
    lab.sql('p', 'UPDATE evidence SET id=201 WHERE id=1; DELETE FROM evidence WHERE id=2', desired)
    lab.wait(lambda: lab.sql('s', 'SELECT count(*) FROM evidence WHERE id=201') == '1')
    lab.wait(lambda: lab.sql('s', 'SELECT count(*) FROM evidence') == '100')
    lab.wait(lambda: lab.sql('r', 'SELECT count(*) FROM evidence') == '100')
