from products.fbasecman.regression.suites.stable.manifest import WORKLOADS


def runtime_context(settings, environment, mode):
    """Resolve product dependencies; never reinterpret the backend as a proxy."""
    if mode == 'direct':
        return None
    from pathlib import Path
    from .test_settings import resolve
    value = resolve(settings, environment, inspect=True)
    if not Path(value['psql']).is_file():
        raise ValueError('本机 psql 不可用')
    return {**value, 'module': 'products.fbasecman.workload_runtime', 'binary': value['fbasecman_bin']}


def catalog(settings, environment):
    return [{
        'id': item.target, 'title': item.summary, 'description': item.name,
        'driver': item.kind, 'action': None,
        'category': 'HA / 配置变更' if item.feature and item.feature != 'jdbc' else '产品常稳',
        'impact': '环境变更' if item.feature and item.feature != 'jdbc' else '产品专用场景',
        'available': False,
        'reason': '专用夹具、目标解析与恢复尚未接入平台 SDK；不调用旧 stable.sh',
        'defaults': {}, 'default_selected': False, 'editable_script': False,
        'source': '产品 stable 工作负载清单',
    } for item in WORKLOADS]
