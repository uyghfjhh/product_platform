def separation_requirements(roles=None, require_enabled=True):
    requirements = {
        "plugins": ["fbase_mac"],
        "writable_node": True,
        "node": "primary",
        "settings": [
            {
                "name": "shared_preload_libraries",
                "contains": "fbase_mac",
                "purpose": "预加载等保插件，使三权分立权限钩子生效",
            },
        ],
    }
    if require_enabled:
        requirements["settings"].append({
            "name": "fdb.separate_user",
            "equals": "on",
            "purpose": "启用三权分立机制",
        })
    if roles:
        requirements["roles"] = list(roles)
    return requirements


COMMON_PREREQUISITES = [
    "mac 集群已由平台创建并处于运行状态",
    "fbase_mac 已创建并预加载",
    "fdb.separate_user=on 已生效",
]
