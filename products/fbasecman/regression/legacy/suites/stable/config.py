"""Stable configuration defaults and refactored fbasecman rendering."""

from pathlib import Path

from framework.configuration import load_config


LEGACY_FIELDS = (
    "write_datasource_names", "read_datasource_names", "parted_datasource_names",
    "promoted_datasource_name", "primary_replica_maps",
)


DEFAULTS = {
    "pgbench_duration": "40m",
    "jdbc_duration": "20m",
    "interval_seconds": 60,
    "server_lifetime": 60,
    "global_prepared_statements_limit": 2000,
    "backend_prepared_statements_limit": 2000,
    "ha_commands": {"enabled": True},
    "reload_status_toggle": {
        "enabled": True, "datasource": "pg_240", "interval_seconds": 0.2,
    },
    "jdbc": {
        "enabled": True,
        "long_conn_clients": 0, "short_conn_clients": 100,
        "shared_sql_count": 2000, "private_sql_count": 200,
        "short_conn_batch": 1, "short_conn_idle_ms": 5,
        "rw_switch_enabled": True, "rw_switch_interval_ops": 100,
        "heartbeat_enabled": True, "heartbeat_interval_ops": 50,
        "guc_enabled": True, "guc_interval_ops": 100, "guc_distinct_count": 200,
    },
}


def _merge(base, override):
    result = dict(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = value
    return result


class StableConfig(object):
    def __init__(self, root, extra_configs=None):
        self.root = Path(root)
        self.runtime_config = load_config(
            self.root, "stable.yaml", extra_configs=extra_configs,
            validate=True, profile="stable",
        )
        self.values = _merge(DEFAULTS, self.runtime_config.config.get("stable", {}))
        self.output_dir = self.runtime_config.output_dir / "stable"
        self.runtime_dir = self.output_dir / "runtime"
        self.state_file = self.runtime_dir / "state.json"

    def duration(self, kind):
        return parse_duration(self.values["%s_duration" % kind])


def parse_duration(value):
    import re
    text = str(value).strip().lower().replace(" ", "")
    matches = list(re.finditer(r"(\d+)([smhd])", text))
    if not matches or "".join(item.group(0) for item in matches) != text:
        raise ValueError("invalid duration %r; use values such as 20m, 1h30m" % value)
    factors = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    return sum(int(item.group(1)) * factors[item.group(2)] for item in matches)


def render_fbasecman_config(cfg, run_dir, main_port, write_port,
                            debug_logging=False, product_log=None):
    db = cfg.runtime_config.config["database"]
    ports = db["ports"]
    log = Path(product_log) if product_log else Path(run_dir) / "logs" / "fbasecman.log"
    pid = Path(run_dir) / "fbasecman.pid"
    locks = Path(run_dir) / "locks"
    prom = main_port + 2
    lines = [
        '# Stable production-like configuration / 稳定环境线上仿真配置',
        '# Keep comments and alignment intentionally mixed for writer coverage.',
        '',
        'pid_file "%s"' % pid, 'locks_dir "%s"' % locks,
        'unix_socket_dir "/tmp"', 'unix_socket_mode "0644"', 'daemonize yes',
        'log_format "%p %t %l [%i %s] (%c) %m\\n"', 'log_to_stdout no',
        'log_syslog no', 'log_debug %s' % ("yes" if debug_logging else "no"), 'log_config yes', 'log_session no',
        # Route and health diagnostics remain in fbasecman.log. PostgreSQL's
        # log_statement=all window is the authoritative SQL evidence, so
        # duplicating every workload query here would create multi-GB logs.
        'log_query no', 'log_stats yes', 'log_file "%s"' % log,
        'log_min_messages "%s"' % cfg.runtime_config.config["fbasecman"].get("log_level", "info"),
        'promhttp_server_port %s' % prom,
        'global_prepared_statements_limit %s' % cfg.values["global_prepared_statements_limit"],
        'backend_prepared_statements_limit %s' % cfg.values["backend_prepared_statements_limit"],
        # The system listener runs in a coroutine and its startup logging can
        # exceed the 4-page default stack on this platform.
        'coroutine_stack_size 16',
        'enable_guc_sync yes', 'heartbeat_request "SELECT 10086"',
        'admin_database "console"', 'host "*"', 'ports "%s,%s"' % (write_port, main_port),
        'backlog 128', 'compression yes',
        'license_dir "%s"' % cfg.runtime_config.config["fbasecman"]["license_dir"], '',
    ]
    datasources = [
        ("pg_220", db["mmr_host"], ports["mmr1"], "mmr_cluster_1", ""),
        ("pg_240", db["mmr_host"], ports["mmr1_standby1"], "mmr_cluster_1", "pg_240"),
        ("pg_241", db["mmr_host"], ports["mmr1_standby2"], "mmr_cluster_1", "pg_241"),
        ("pg_242", db["mmr_host"], ports["mmr1_standby3"], "mmr_cluster_1", "pg_242"),
        ("pg_230", db["mmr_host"], ports["mmr2"], "mmr_cluster_2", ""),
        ("pg_250", db["mmr_host"], ports["mmr2_standby1"], "mmr_cluster_2", "pg_250"),
        ("pg_251", db["mmr_host"], ports["mmr2_standby2"], "mmr_cluster_2", "pg_251"),
        ("pg_252", db["mmr_host"], ports["mmr2_standby3"], "mmr_cluster_2", "pg_252"),
        ("rep_pg_220", db["mmr_host"], ports["mmr1"], "rep_cluster", ""),
        ("rep_pg_230", db["mmr_host"], ports["mmr1_standby1"], "rep_cluster", "pg_230"),
        ("rep_pg_240", db["mmr_host"], ports["mmr1_standby2"], "rep_cluster", "pg_240"),
    ]
    for name, host, port, cluster, appname in datasources:
        lines.extend(['# datasource %s: backend endpoint / 后端端点' % name,
                      'datasources "%s" {' % name, '\thost      "%s"   # storage host / 后端地址' % host,
                      '    port %s' % port, '    cluster_name "%s"' % cluster,
                      '    status "active"'])
        if appname:
            lines.append('    application_name "%s"' % appname)
        lines.extend(['    weight 10', '    server_max_routing 100', '    tls "disable"', '}', '', ''])
    groups = [
        ("mmrhint", "mmr", "    backend_clusters \"mmr_cluster_1,mmr_cluster_2\"\n    write_cluster \"mmr_cluster_1\"\n    promoted_cluster \"mmr_cluster_2\""),
        ("mmrhint_b", "mmr", "    backend_clusters \"mmr_cluster_1,mmr_cluster_2\"\n    write_cluster \"mmr_cluster_1\"\n    promoted_cluster \"mmr_cluster_2\""),
        ("mmrport", "mmr", "    backend_clusters \"mmr_cluster_1,mmr_cluster_2\"\n    write_cluster \"mmr_cluster_1\"\n    promoted_cluster \"mmr_cluster_2\"\n    write_port %s" % write_port),
        ("mmrport_b", "mmr", "    backend_clusters \"mmr_cluster_1,mmr_cluster_2\"\n    write_cluster \"mmr_cluster_1\"\n    promoted_cluster \"mmr_cluster_2\"\n    write_port %s" % write_port),
        ("rephint", "replication", "    backend_clusters \"rep_cluster\""),
        ("rephint_b", "replication", "    backend_clusters \"rep_cluster\""),
        ("balance", "balance", "    backend_clusters \"mmr_cluster_1,mmr_cluster_2\"\n    access_mode \"read_write\""),
        ("balance_b", "balance", "    backend_clusters \"mmr_cluster_1,mmr_cluster_2\"\n    access_mode \"read_write\""),
    ]
    for name, mode, body in groups:
        lines.extend(['# group %s: routing policy / 路由策略' % name,
                      'group "%s" {' % name, '    group_mode "%s"' % mode,
                      '    storage_db "postgres"', body, '    check "auto"', '}', ''])
    for user, group_names, method in (("mmrhint", "mmrhint,mmrhint_b", "hint"),
                                      ("mmrhint_aux", "mmrhint", "hint"),
                                      ("mmrhint_b", "mmrhint_b", "hint"),
                                      ("mmrport", "mmrport,mmrport_b", "port"),
                                      ("mmrport_aux", "mmrport", "port"),
                                      ("mmrport_b", "mmrport_b", "port"),
                                      ("rephint", "rephint,rephint_b", "hint"),
                                      ("rephint_aux", "rephint", "hint"),
                                      ("rephint_b", "rephint_b", "hint"),
                                      ("balance", "balance,balance_b", "none"),
                                      ("balance_aux", "balance", "none"),
                                      ("balance_b", "balance_b", "none")):
        lines.extend(['user "%s" {' % user, '    group_names "%s"' % group_names,
                      '    authentication "none"', '    storage_user "postgres"',
                      '    rw_split_method "%s"' % method, '    pool "transaction"',
                      '    pool_size 300', '    server_lifetime %s' % cfg.values["server_lifetime"],
                      '    pool_discard no', '    pool_reserve_prepared_statement yes', '}', ''])
    lines.extend(['user "admin" {', '    authentication "none"', '    pool "session"',
                  '    role "admin"', '}'])
    rendered = "\n".join(lines) + "\n"
    for field in LEGACY_FIELDS:
        if field in rendered:
            raise ValueError("legacy stable field rendered: %s" % field)
    return rendered
