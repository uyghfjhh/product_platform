"""Register an existing Kingbase primary/standby pair after read-only identity checks."""

import yaml
from fastapi import APIRouter, HTTPException
from platform_app.api.schemas import IDENTIFIER
from platform_app.resources import canonical_host, validate_registration
from pydantic import BaseModel, ConfigDict, Field

from products.kingbase.database import identity, upstream_matches


class ExistingEnvironment(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(pattern=IDENTIFIER)
    title: str = Field(min_length=1, max_length=120)
    host: str = Field(default='127.0.0.1', pattern=r'^[a-zA-Z0-9][a-zA-Z0-9.:-]*$', max_length=253)
    port: int = Field(default=54321, ge=1, le=65535)
    database_name: str = Field(default='kingbase', min_length=1, max_length=63)
    database_user: str = Field(default='kingbase', min_length=1, max_length=63)
    standby_host: str = Field(default='', pattern=r'^$|^[a-zA-Z0-9][a-zA-Z0-9.:-]*$', max_length=253)
    standby_port: int | None = Field(default=None, ge=1, le=65535)


def verify_pair(environment, primary, standby_endpoint, standby):
    if primary['recovery']:
        raise ValueError('主节点连接实际指向备库')
    if not standby:
        return
    if not standby['recovery']:
        raise ValueError('备节点连接实际指向主库')
    if primary['system_identifier'] != standby['system_identifier']:
        raise ValueError('主备数据库系统标识不一致')
    if (canonical_host(environment['host']), environment['port']) == (canonical_host(standby_endpoint['host']), standby_endpoint['port']):
        raise ValueError('主备不能使用同一连接端点')
    receivers = [r for r in standby['receivers'] if r['status'] == 'streaming']
    if len(receivers) != 1:
        raise ValueError('备库没有处于 streaming 状态的接收连接')
    receiver = receivers[0]
    if not upstream_matches(environment, standby_endpoint, receiver):
        raise ValueError('备库当前上游与登记主节点不一致')
    if not any(r['state'] == 'streaming' for r in primary['senders']):
        raise ValueError('主库未观察到 streaming 复制发送连接')


def create_router(settings, store):
    router = APIRouter()

    @router.post('/api/v1/products/kingbase/environments', status_code=201)
    def register_existing(item: ExistingEnvironment):
        if store.environments.get_environment(item.id):
            raise HTTPException(409, '环境 ID 已存在')
        environment = {key: value for key, value in item.model_dump().items() if not key.startswith('standby_')}
        environment['product_id'] = 'kingbase'
        standby_endpoint = {'host': item.standby_host or item.host, 'port': item.standby_port}
        try:
            primary = identity(environment)
            standby = identity(environment, standby_endpoint) if item.standby_port else None
            verify_pair(environment, primary, standby_endpoint, standby)
        except Exception as exc:
            raise HTTPException(422, str(exc) if isinstance(exc, ValueError) else '金仓实例连接或身份探测失败，请检查地址、用户和免密认证') from exc
        config = {'product_id': 'kingbase', 'database_mode': primary.get('database_mode'),
                  'hosts': {'primary_host': {'address': item.host}},
                  'instances': {'primary': {'host': 'primary_host', 'port': item.port, 'data_dir': primary['data_dir']}},
                  'streaming_clusters': {'kingbase': {'primary': 'primary', 'standbys': [], 'replication': {'mode': 'async'}}}}
        if standby:
            config['hosts']['standby_host'] = {'address': standby_endpoint['host']}
            config['instances']['standby'] = {'host': 'standby_host', 'port': item.standby_port, 'data_dir': standby['data_dir']}
            config['streaming_clusters']['kingbase']['standbys'].append({'instance': 'standby'})
            modes = {r['sync_state'] for r in primary['senders'] if r['state'] == 'streaming'}
            config['streaming_clusters']['kingbase']['replication']['mode'] = 'sync' if modes == {'sync'} else 'async'
        directory = settings.profile_dir(item.id)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / 'kingbase.yaml'
        try:
            with path.open('x') as handle:
                handle.write(yaml.safe_dump(config, sort_keys=False, allow_unicode=True))
        except FileExistsError as exc:
            raise HTTPException(409, '此环境 ID 已有金仓配置，请使用其他 ID 或检查原配置') from exc
        environment.update(deployment_config=str(path), deployment_target='streaming.kingbase')
        try:
            return store.environments.put_environment(environment, validator=validate_registration)
        except Exception:
            path.unlink()
            raise

    return router
