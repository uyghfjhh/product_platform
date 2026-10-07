import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from ..actions import actions_for_environment
from ..providers import provider_extensions
from ..monitoring import observation_fingerprint
from . import PRESETS, WorkloadInput, resolve_driver
from .sql import environment_fingerprint
from .defaults import PROFILES, default_jdbc_jar


class Selection(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(min_length=1, max_length=100)
    instance_id: str | None = Field(default=None, pattern=r'^[A-Za-z0-9_.-]{1,100}$')
    title: str | None = Field(default=None, min_length=1, max_length=120)
    parameters: dict = Field(default_factory=dict)


class PlanInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    environment_id: str
    workloads: list[Selection] = Field(min_length=1, max_length=30)
    mode: Literal['sequential'] = 'sequential'
    smoke: bool = False


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def plan_digest(plan):
    return digest({key: plan.get(key) for key in (
        'environment_id', 'environment_fingerprint', 'steps', 'workloads', 'mode', 'expires_at', 'monitoring_fingerprint',
    )})


class Workbench:
    def __init__(self, settings, store, orchestrator, monitoring=None):
        self.settings, self.store, self.orchestrator = settings, store, orchestrator
        self.monitoring = monitoring

    def environment(self, identity):
        environment = self.store.environments.get_environment(identity)
        if not environment:
            raise ValueError('环境不存在')
        return environment

    def catalog(self, identity):
        environment = self.environment(identity)
        actions = {item['id'] for item in actions_for_environment(environment, self.settings)}
        workloads = []
        jdbc_jar = default_jdbc_jar(self.settings, environment) if 'workload.jdbc' in actions else ''
        for name, title, description, driver, preset, reconnect in (
            ('pgbench.connectivity', '连接与轻查询', '验证连接及轻查询持续执行', 'pgbench', 'connectivity', False),
            ('pgbench.catalog', '系统目录查询', '持续查询数据库系统目录', 'pgbench', 'catalog', False),
            ('pgbench.reconnect', '短连接压力', '每个事务重新建立连接，观察连接周转', 'pgbench', 'connectivity', True),
            ('jdbc.connectivity', 'JDBC 查询负载', '使用 JDBC 客户端持续执行查询', 'jdbc', 'connectivity', False),
        ):
            defaults = WorkloadInput(preset=preset, connect_per_transaction=reconnect).model_dump(exclude={'environment_fingerprint'})
            defaults['script'] = PRESETS[preset]
            if driver == 'jdbc':
                defaults['jdbc_jar'] = jdbc_jar
            available = 'workload.' + driver in actions
            workloads.append({
                'id': name, 'title': title, 'description': description, 'driver': driver,
                'action': 'workload.' + driver, 'category': 'SQL 负载', 'impact': '只读事务',
                'available': available, 'reason': None if available else '当前产品未声明数据库负载能力',
                'defaults': defaults, 'default_selected': name == 'pgbench.connectivity' and available,
                'editable_script': available, 'source': '平台内置查询模板',
            })
        hook = provider_extensions(self.settings, environment['product_id']).workload_catalog
        if hook:
            workloads.extend(hook(self.settings, environment))
        runtime_hook = provider_extensions(self.settings, environment['product_id']).workload_runtime
        if runtime_hook:
            for entry in workloads:
                if entry['action'] in {'workload.pgbench', 'workload.jdbc'}:
                    entry['defaults']['connection_mode'] = 'proxy'
        identifiers = [item['id'] for item in workloads]
        if len(set(identifiers)) != len(identifiers):
            raise ValueError('负载目录包含重复标识')
        return {
            'environment': {key: environment.get(key) for key in (
                'id', 'title', 'product_id', 'host', 'port', 'database_name', 'database_user',
            )},
            'environment_fingerprint': environment_fingerprint(environment),
            'workloads': workloads, 'modes': ['sequential'], 'profiles': list(PROFILES),
            'connection_modes': ['proxy', 'compare', 'direct'] if runtime_hook else ['direct'],
            'limitations': ['第一阶段支持顺序执行；并发场景尚未接入。',
                            '自定义脚本仅支持 SELECT，执行于只读事务；不支持 pgbench 元命令。'],
        }

    def preview(self, item):
        environment = self.environment(item.environment_id)
        catalog = self.catalog(item.environment_id)
        entries = {value['id']: value for value in catalog['workloads']}
        identities = [selection.instance_id or selection.id for selection in item.workloads]
        if len(set(identities)) != len(identities):
            raise ValueError('不能重复选择同一负载实例')
        steps, workloads, issues = [], [], []
        fingerprint = catalog['environment_fingerprint']
        for selection in item.workloads:
            definition = entries.get(selection.id)
            if not definition or not definition['available']:
                raise ValueError('负载不可执行：' + selection.id)
            if definition['action'] not in {'workload.pgbench', 'workload.jdbc'}:
                raise ValueError('负载尚未接入平台执行器')
            values = {**definition['defaults'], **selection.parameters, 'environment_fingerprint': fingerprint}
            if item.smoke:
                values.update(clients=1, jobs=None, duration_seconds=min(values['duration_seconds'], 10),
                              target_tps=0, minimum_tps=0, max_average_latency_ms=0)
            options = WorkloadInput.model_validate(values)
            tested = None
            try:
                resolve_driver(environment, definition['driver'], options)
                hook = provider_extensions(self.settings, environment['product_id']).workload_runtime
                if options.connection_mode != 'direct':
                    if not hook:
                        raise ValueError('当前产品未提供代理负载执行器')
                    tested = hook(self.settings, environment, options.connection_mode)
                    options.tested_build_sha256 = tested['sha256']
            except ValueError as exc:
                issues.append(f"{selection.title or definition['title']}：{exc}")
            options.script = options.script or PRESETS[options.preset]
            steps.append({'action': definition['action'], 'target': selection.instance_id or selection.id,
                          'parameters': options.model_dump()})
            workloads.append({
                'id': selection.instance_id or selection.id, 'template_id': selection.id,
                'title': selection.title or definition['title'], 'driver': definition['driver'],
                'source': definition['source'], 'script_sha256': hashlib.sha256(options.script.encode()).hexdigest(),
                'script_modified': options.script != definition['defaults']['script'],
                'tested_build': {key: tested.get(key) for key in ('binary', 'sha256', 'version')}
                if tested else None,
            })
        try:
            monitoring_fingerprint = observation_fingerprint(self.settings, environment)
        except (ValueError, OSError, KeyError):
            monitoring_fingerprint = None
        row = {
            'monitoring_fingerprint': monitoring_fingerprint,
            'title': '负载方案 · ' + environment['title'], 'workbench': True,
            'environment_id': environment['id'], 'environment': catalog['environment'],
            'environment_fingerprint': fingerprint, 'steps': steps, 'workloads': workloads,
            'mode': item.mode, 'smoke': item.smoke, 'ready': not issues, 'issues': issues,
            'duration_seconds': sum(step['parameters']['duration_seconds'] *
                                    (2 if step['parameters']['connection_mode'] == 'compare' else 1)
                                    for step in steps),
            'peak_clients': max(step['parameters']['clients'] for step in steps),
            'expires_at': (datetime.now(UTC) + timedelta(minutes=30)).isoformat(),
        }
        row['sha256'] = plan_digest(row)
        return self.store.orchestration.put('pipelines', row, 'workload-' + uuid4().hex)

    def start(self, identity, acknowledged):
        if not acknowledged:
            raise ValueError('请确认已审阅负载、参数及实际运行目标')
        plan = self.store.orchestration.get('pipelines', identity)
        if not plan or not plan.get('workbench'):
            raise ValueError('负载计划不存在')
        if plan.get('sha256') != plan_digest(plan):
            raise ValueError('负载计划已修改，请重新审阅')
        if not plan['ready'] or datetime.now(UTC) > datetime.fromisoformat(plan['expires_at']):
            raise ValueError('负载计划不可执行或已过期，请重新审阅')
        environment = self.environment(plan['environment_id'])
        if environment_fingerprint(environment) != plan['environment_fingerprint']:
            raise ValueError('审阅后环境发生变化，请重新生成计划')
        for step in plan['steps']:
            options = WorkloadInput.model_validate(step['parameters'])
            resolve_driver(environment, step['action'].split('.')[1], options)
            if options.connection_mode != 'direct':
                hook = provider_extensions(self.settings, environment['product_id']).workload_runtime
                if not hook:
                    raise ValueError('当前产品未提供代理负载执行器')
                tested = hook(self.settings, environment, options.connection_mode)
                if options.tested_build_sha256 != tested['sha256']:
                    raise ValueError('审阅后 fbasecman 构建文件已变化，请重新生成计划')
        if self.monitoring and plan.get('monitoring_fingerprint'):
            self.monitoring.enable(environment['id'], True)
        return self.orchestrator.start(identity, True, 'workload-workbench', run_id=identity)

    def run(self, identity):
        row = self.store.orchestration.get('runs', identity)
        if not row or not row['definition'].get('workbench'):
            raise ValueError('负载执行不存在')
        return {**row, 'task_details': [self.store.tasks.get_task(task) for task in row['tasks']]}
