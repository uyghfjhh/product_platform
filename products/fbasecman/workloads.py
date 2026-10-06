from products.fbasecman.regression.suites.stable.manifest import WORKLOADS


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
