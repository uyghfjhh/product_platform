from pathlib import Path

from products.fbasecman.regression.suites.stable.manifest import WORKLOADS

_ASSETS_DIR = Path(__file__).parent / "regression" / "suites" / "stable" / "assets" / "pgbench"

_MMR_POOL = ("pg_220", "pg_230", "pg_240", "pg_241", "pg_242", "pg_250", "pg_251", "pg_252")
_REP_POOL = ("rep_pg_220", "rep_pg_230", "rep_pg_240")
ROUTE_RULES = {
    "pgbench.mmr_hint_long": ("mmrhint", _MMR_POOL, 1),
    "pgbench.mmr_hint_short": ("mmrhint", _MMR_POOL, 1),
    "pgbench.mmr_port_read": ("mmrport", _MMR_POOL, 1),
    "pgbench.mmr_port_write": ("mmrport", _MMR_POOL, 1),
    "pgbench.rep_hint": ("rephint", _REP_POOL, 1),
    "pgbench.balance": ("balance", ("pg_220", "pg_230"), 2),
    "pgbench.console": ("admin", ("default_console",), 1),
    "pgbench.reload_status_toggle": ("mmrhint", _MMR_POOL, 1),
    "pgbench.ha_node_state": ("admin", ("default_console",), 1),
    "pgbench.ha_group_route": ("admin", ("default_console",), 1),
    "pgbench.ha_weight": ("admin", ("default_console",), 1),
    "pgbench.ha_cluster": ("admin", ("default_console",), 1),
    "jdbc.prepared_leak": ("mmrhint", _MMR_POOL, 1),
}

_JDBC_SCRIPT_PREVIEW = """-- PreparedLeakMain.java (JDBC 预编译缓存与多特性并发常稳)
-- 连接串: jdbc:postgresql://<proxy_host>:<main_port>/mmrhint (user=mmrhint, prepareThreshold=1)
-- 默认并发: short_conn_clients=100 (短连接高频建连/断连), shared_sql_count=2000, private_sql_count=200
1. PreparedStatement 缓存压力: 2000 条共享 SQL + 每线程 200 条私有 SQL 混合执行与关闭回收
2. 读写事务切换校验 (每 100 ops): SET SESSION CHARACTERISTICS AS TRANSACTION READ WRITE / READ ONLY
3. 连接保活心跳校验 (每 50 ops): SELECT 10086
4. 会话 GUC 同步校验 (每 100 ops): 200 组参数交替 SET 与 SHOW 对照，要求零错乱 (guc_failures=0)
"""


def runtime_context(settings, environment, mode):
    """Resolve product dependencies; never reinterpret the backend as a proxy."""
    if mode == 'direct':
        return None
    from .test_settings import resolve
    value = resolve(settings, environment, inspect=True)
    if not Path(value['psql']).is_file():
        raise ValueError('本机 psql 不可用')
    return {**value, 'module': 'products.fbasecman.workload_runtime', 'binary': value['fbasecman_bin']}


def _load_script_preview(item) -> str:
    if item.sql_asset:
        sql_path = _ASSETS_DIR / item.sql_asset
        if sql_path.is_file():
            return sql_path.read_text(encoding="utf-8").strip()
    return _JDBC_SCRIPT_PREVIEW.strip()


def catalog(settings, environment):
    entries = []
    for item in WORKLOADS:
        route_user, allowed_nodes, min_nodes = ROUTE_RULES.get(
            item.name, (item.user or "mmrhint", set(), 1)
        )
        is_ha = bool(item.feature and item.feature != 'jdbc')
        duration_sec = 1200 if item.kind == 'jdbc' else 2400
        duration_text = '20 分钟' if item.kind == 'jdbc' else '40 分钟'
        clients = 100 if item.kind == 'jdbc' else item.clients
        jobs = 100 if item.kind == 'jdbc' else item.jobs
        script_preview = _load_script_preview(item)
        entries.append({
            'id': item.target,
            'title': item.summary,
            'description': item.name,
            'driver': item.kind,
            'action': None,
            'category': 'HA / 配置变更' if is_ha else '产品常稳',
            'impact': '环境变更' if is_ha else '产品专用场景',
            'available': False,
            'reason': '专用夹具、目标解析与恢复通过 fbasecman 常稳引擎（stability.fbasecman）独立执行',
            'defaults': {
                'clients': clients,
                'jobs': jobs,
                'duration_seconds': duration_sec,
                'preset': 'connectivity',
                'script': script_preview,
                'target_tps': 0,
                'connect_per_transaction': bool(item.connect_per_transaction or item.kind == 'jdbc'),
                'statement_timeout_seconds': 30,
                'minimum_tps': 0,
                'max_average_latency_ms': 0,
                'jdbc_jar': '',
                'connection_mode': 'proxy',
            },
            'default_selected': False,
            'editable_script': False,
            'source': '产品 stable 工作负载清单',
            'stable_meta': {
                'name': item.name,
                'user': item.user or 'mmrhint',
                'database': item.database or 'mmrhint',
                'port_kind': item.port_kind,
                'clients': clients,
                'jobs': jobs,
                'connect_per_transaction': bool(item.connect_per_transaction or item.kind == 'jdbc'),
                'feature': item.feature,
                'duration_seconds': duration_sec,
                'duration_text': duration_text,
                'sql_asset': item.sql_asset or 'PreparedLeakMain.java',
                'script_preview': script_preview,
                'route_rule': {
                    'user': route_user,
                    'allowed_nodes': sorted(allowed_nodes),
                    'min_nodes': min_nodes,
                },
                'verifications': [
                    {
                        'title': '代理进程与内存守护',
                        'detail': 'fbasecman PID 持续存活、无 Core Dump，全程采集 RSS 内存曲线与文件句柄趋势',
                    },
                    {
                        'title': '客户端零非预期报错',
                        'detail': (
                            'HA 控制命令允许预期内配置锁重试 (reload_busy)，其余客户端非预期错误数必须为 0'
                            if is_ha
                            else '事务执行成功数 > 0，且客户端非预期失败、读写切换失败、心跳与 GUC 失败数必须全部为 0'
                        ),
                    },
                    {
                        'title': '代理路由规则命中验证',
                        'detail': f'核对日志 (routing) matched rule：账号 {route_user} 必须落在允许节点池内（至少命中 {min_nodes} 个节点，0 越界）',
                    },
                    {
                        'title': '后端 PostgreSQL 日志扫描',
                        'detail': '采集 mmr1 / mmr2 运行时间窗日志，确认 table_test / test_prepare 无 ERROR、FATAL、PANIC',
                    },
                ],
            },
        })
    return entries
