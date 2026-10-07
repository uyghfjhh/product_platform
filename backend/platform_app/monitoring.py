"""Shared background observations and bounded SQLite history."""
import hashlib
import json
import logging
import sqlite3
import time
import fcntl
import threading
from pathlib import Path
from datetime import datetime
from contextlib import contextmanager

from fastapi.encoders import jsonable_encoder

from . import monitoring_history, postgres_monitoring, monitoring_hosts
from .monitoring_events import deployment_events, transitions
from .monitoring_alerts import DEFAULT_RULES, evaluate, evaluate_links, evaluate_members, evaluate_hosts, evaluate_database, validate_rules
from .providers import provider_for
from .topology import configured_topology


def observation_fingerprint(settings, environment):
    topology = configured_topology(settings, environment)
    path = environment.get('deployment_config')
    config_digest = hashlib.sha256(Path(path).read_bytes()).hexdigest() if path else ''
    return hashlib.sha256(json.dumps([environment, topology['nodes'], config_digest], sort_keys=True, default=str).encode()).hexdigest()


class MonitoringService:
    def __init__(self, settings, store):
        self.settings, self.store = settings, store
        self._task_cache = {}
        self._task_cache_lock = threading.RLock()
        self.directory = settings.data_dir / 'monitoring'
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / 'samples.sqlite3'
        with self.connection() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS host_paths (environment TEXT,identity TEXT,path TEXT,stamp REAL,PRIMARY KEY(environment,identity,path));
                CREATE TABLE IF NOT EXISTS host_metric_seen (environment TEXT,identity TEXT,stamp REAL,PRIMARY KEY(environment,identity));
                CREATE TABLE IF NOT EXISTS host_samples (identity TEXT PRIMARY KEY,stamp REAL,payload TEXT);
                CREATE TABLE IF NOT EXISTS minute_metrics (environment TEXT,bucket INTEGER,series TEXT,label TEXT,unit TEXT,total REAL,count INTEGER,peak REAL,PRIMARY KEY(environment,bucket,series));
                CREATE TABLE IF NOT EXISTS events (environment TEXT, stamp REAL, payload TEXT);
                CREATE INDEX IF NOT EXISTS events_lookup ON events(environment,stamp);
                CREATE TABLE IF NOT EXISTS enabled (id TEXT PRIMARY KEY);
                CREATE TABLE IF NOT EXISTS rules (environment TEXT PRIMARY KEY, payload TEXT);
                CREATE TABLE IF NOT EXISTS latest (environment TEXT PRIMARY KEY, payload TEXT);
                CREATE TABLE IF NOT EXISTS samples (environment TEXT, stamp REAL, fingerprint TEXT, payload TEXT);
                CREATE INDEX IF NOT EXISTS sample_lookup ON samples(environment, stamp);''')

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=5)
        try:
            with db:
                yield db
        finally:
            db.close()

    def enable(self, identity, enabled):
        with self.connection() as db:
            if enabled:
                db.execute('INSERT OR IGNORE INTO enabled VALUES (?)', (identity,))
            else:
                db.execute('DELETE FROM enabled WHERE id=?', (identity,))

    def rules(self, identity, value=None):
        with self.connection() as db:
            if value is not None:
                validate_rules(value)
                db.execute('INSERT OR REPLACE INTO rules VALUES (?,?)', (identity, json.dumps(value)))
                # Keep history; the collector resets only alert evidence when rules change.
            row = db.execute('SELECT payload FROM rules WHERE environment=?', (identity,)).fetchone()
        return {**DEFAULT_RULES,**json.loads(row[0])} if row else dict(DEFAULT_RULES)

    def task_events(self, identity, window_minutes):
        key=(identity,window_minutes)
        with self._task_cache_lock:
            cached=self._task_cache.get(key)
            if cached and time.monotonic()-cached[0]<15:
                return cached[1]
            events=deployment_events(self.store.tasks.list_tasks(100,include_archived=True,environment_id=identity),time.time()-window_minutes*60)
            if len(self._task_cache)>=256:
                self._task_cache.pop(min(self._task_cache,key=lambda k:self._task_cache[k][0]))
            self._task_cache[key]=(time.monotonic(),events)
            return events

    def read(self, identity, window_minutes=60):
        if window_minutes not in (15,60,360,1440,10080):
            raise ValueError('不支持的历史时间范围')
        with self.connection() as db:
            series = monitoring_history.read(db, identity, time.time()-window_minutes*60, window_minutes)
            enabled = db.execute('SELECT 1 FROM enabled WHERE id=?', (identity,)).fetchone() is not None
            rows = db.execute('SELECT stamp,payload FROM samples WHERE environment=? AND stamp>? ORDER BY stamp DESC LIMIT 120', (identity, time.time()-min(window_minutes*60,86400))).fetchall()
            events = db.execute('SELECT payload FROM events WHERE environment=? AND stamp>? ORDER BY stamp DESC LIMIT 100', (identity, time.time()-min(window_minutes*60,86400))).fetchall()
            current = db.execute('SELECT payload FROM latest WHERE environment=?', (identity,)).fetchone()
        samples = [json.loads(row[1]) for row in reversed(rows)]
        task_events = self.task_events(identity,window_minutes)
        merged = sorted([json.loads(r[0]) for r in events] + task_events, key=lambda event: datetime.fromisoformat(event['observed_at']).timestamp(), reverse=True)[:100]
        return {'enabled': enabled, 'interval_seconds': 15, 'latest': json.loads(current[0]) if current else None, 'history': samples, 'rules': self.rules(identity), 'events': merged, 'series':series, 'window_minutes':window_minutes}

    def metrics(self, identity):
        from .monitoring_export import exposition
        with self.connection() as db:
            enabled=db.execute('SELECT 1 FROM enabled WHERE id=?',(identity,)).fetchone() is not None
            row=db.execute('SELECT payload FROM latest WHERE environment=?',(identity,)).fetchone()
        return exposition(identity,enabled,json.loads(row[0]) if row else None)

    def tick(self):
        # One collector per shared data directory, including multiple web workers.
        with (self.directory / 'collector.lock').open('a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return
            with self.connection() as db:
                identities = [r[0] for r in db.execute('SELECT id FROM enabled')]
                db.execute('DELETE FROM samples WHERE stamp<?',(time.time()-86400,))
                db.execute('DELETE FROM events WHERE stamp<?',(time.time()-86400,))
                db.execute('DELETE FROM minute_metrics WHERE bucket<?',(time.time()-7*86400,))
                db.execute('DELETE FROM host_paths WHERE stamp<?',(time.time()-120,))
                db.execute('DELETE FROM host_samples WHERE stamp<?',(time.time()-7*86400,))
                db.execute('DELETE FROM host_metric_seen WHERE stamp<?',(time.time()-7*86400,))
            for identity in identities:
                environment = self.store.environments.get_environment(identity)
                if environment is None:
                    self.enable(identity, False)
                    continue
                try:
                    provider = provider_for(self.settings, environment['product_id'])
                    collect = getattr(provider, 'monitoring_snapshot', None)
                    if collect is None:
                        continue
                    topology = configured_topology(self.settings, environment)
                    nodes = topology['nodes']
                    fingerprint = observation_fingerprint(self.settings, environment)
                    with self.connection() as db:
                        last = db.execute('SELECT stamp,fingerprint,payload FROM samples WHERE environment=? ORDER BY stamp DESC LIMIT 1', (identity,)).fetchone()
                    if last and last[1] == fingerprint and time.time()-last[0] < 15:
                        continue
                    value = jsonable_encoder(collect(environment, nodes))
                    # History retains lightweight progress only, never conflict rows or error contents.
                    metrics = getattr(provider, 'monitoring_metrics', None)
                    previous = json.loads(last[2]) if last and last[1] == fingerprint else None
                    value['database_metrics'] = postgres_monitoring.derive(value, previous)
                    mmr_metrics = getattr(provider, 'monitoring_mmr_metrics', None)
                    value['mmr_metrics'] = mmr_metrics(value) if callable(mmr_metrics) else []
                    rules = self.rules(identity)
                    value['alert_rules'] = rules
                    alert_previous = previous if previous and previous.get('alert_rules') == rules else None
                    value['alerts'] = evaluate(value, alert_previous, rules)
                    value['replication_metrics'] = metrics(value, previous) if callable(metrics) else []
                    links = getattr(provider, 'monitoring_links', None)
                    value['resolved_topology'] = links(value, topology, previous) if callable(links) else {'members': [], 'links': []}
                    value['alerts'].extend(evaluate_links(value, alert_previous, rules))
                    members = getattr(provider, 'monitoring_members', None)
                    value['member_consensus'] = members(value) if callable(members) else []
                    value['alerts'].extend(evaluate_members(value, alert_previous, rules))
                    try:
                        value['hosts'] = monitoring_hosts.collect(self, environment, nodes)
                    except (OSError, ValueError, KeyError, TypeError):
                        value['hosts'] = [{'host': environment.get('host','未知'), 'valid': False, 'error': '主机配置或 SSH 采集不可用'}]
                    value['alerts'].extend(evaluate_database(value,alert_previous,rules))
                    value['alerts'].extend(evaluate_hosts(value,alert_previous,rules))
                    value['fingerprint'] = fingerprint
                    value['topology'] = {'edges': topology.get('edges', [])}
                    with self.connection() as db:
                        if last and last[1] != fingerprint:
                            db.execute('DELETE FROM samples WHERE environment=?', (identity,))
                        for event in transitions(value, previous):
                            db.execute('INSERT INTO events VALUES (?,?,?)', (identity, time.time(), json.dumps(event)))
                        db.execute('INSERT OR REPLACE INTO latest VALUES (?,?)', (identity, json.dumps(value)))
                        history = {**value, 'nodes': [{**n, 'sections': {k: v for k, v in n['sections'].items() if k in ('runtime', 'timeline', 'slots', 'senders', 'origins','database','wal')}} for n in value['nodes']]}
                        monitoring_history.append(db, identity, value)
                        db.execute('INSERT INTO samples VALUES (?,?,?,?)', (identity, time.time(), fingerprint, json.dumps(history)))
                except Exception:
                    logging.getLogger(__name__).exception('监控采集失败：%s', identity)
