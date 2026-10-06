import os
from pathlib import Path

from platform_regress.clients.jdbc import JdbcError, resolve_jar

from ..product_catalog import discover_products

PROFILES = (
    {'id': 'quick', 'title': '快速验证 · 30 秒', 'duration_seconds': 30, 'clients': 4},
    {'id': 'standard', 'title': '标准负载 · 5 分钟', 'duration_seconds': 300, 'clients': 8},
    {'id': 'soak', 'title': '长时运行 · 30 分钟', 'duration_seconds': 1800, 'clients': 8},
)


def default_jdbc_jar(settings, environment):
    explicit = os.environ.get('PRODUCT_PLATFORM_JDBC_JAR')
    if explicit:
        return str(Path(explicit).expanduser().absolute())
    directories = [settings.product_regress_root(environment['product_id']) / 'lib_jdbc',
                   settings.data_dir / 'drivers' / 'jdbc']
    directories.extend(settings.product_regress_root(manifest.id) / 'lib_jdbc'
                       for manifest in discover_products(settings.products_root).values()
                       if manifest.id != environment['product_id'] and 'database' in manifest.capabilities)
    for directory in directories:
        try:
            jar = resolve_jar(directory)
        except JdbcError:
            continue
        if jar.is_file():
            return str(jar.resolve())
    return ''
