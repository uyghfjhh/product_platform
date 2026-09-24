"""Stable workload definitions."""


class StableWorkload(object):
    def __init__(self, name, kind, summary, duration_key, sql_asset=None,
                 user=None, database=None, port_kind="main", clients=2,
                 jobs=2, connect_per_transaction=False, feature=None):
        self.name = name
        self.kind = kind
        self.summary = summary
        self.duration_key = duration_key
        self.sql_asset = sql_asset
        self.user = user
        self.database = database
        self.port_kind = port_kind
        self.clients = clients
        self.jobs = jobs
        self.connect_per_transaction = connect_per_transaction
        self.feature = feature

    @property
    def target(self):
        return "stable.%s" % self.name


WORKLOADS = (
    StableWorkload("pgbench.mmr_hint_long", "pgbench", "MMR hint 长连接读写切换", "pgbench",
                   "test1.sql", "mmrhint", "mmrhint"),
    StableWorkload("pgbench.mmr_hint_short", "pgbench", "MMR hint 短连接读写切换", "pgbench",
                   "test2.sql", "mmrhint", "mmrhint", connect_per_transaction=True),
    StableWorkload("pgbench.mmr_port_read", "pgbench", "MMR port 读端口", "pgbench",
                   "test3.sql", "mmrport", "mmrport"),
    StableWorkload("pgbench.mmr_port_write", "pgbench", "MMR port 写端口", "pgbench",
                   "test4.sql", "mmrport", "mmrport", port_kind="write", connect_per_transaction=True),
    StableWorkload("pgbench.rep_hint", "pgbench", "REP hint 读写切换", "pgbench",
                   "test5.sql", "rephint", "rephint"),
    StableWorkload("pgbench.balance", "pgbench", "balance 三节点负载", "pgbench",
                   "test6.sql", "balance", "balance", connect_per_transaction=True),
    StableWorkload("pgbench.console", "pgbench", "console 状态查询负载", "pgbench",
                   "test7.sql", "admin", "console"),
    StableWorkload("pgbench.reload_status_toggle", "pgbench",
                   "配置切换 datasource active/parted、RELOAD 与会话读写切换并发压力", "pgbench",
                   "reload_rw_toggle.sql", "mmrhint", "mmrhint", clients=10, jobs=2,
                   connect_per_transaction=True, feature="reload_status_toggle"),
    StableWorkload("pgbench.ha_node_state", "pgbench", "SET NODE ACTIVE/PARTED 并发控制", "pgbench",
                   "ha_node_state.sql", "admin", "console", clients=1, jobs=1, feature="ha_commands"),
    StableWorkload("pgbench.ha_group_route", "pgbench", "SET NODE WRITE/PROMOTED 并发控制", "pgbench",
                   "ha_group_route.sql", "admin", "console", clients=1, jobs=1, feature="ha_commands"),
    StableWorkload("pgbench.ha_weight", "pgbench", "SET NODE WEIGHT 并发控制", "pgbench",
                   "ha_weight.sql", "admin", "console", clients=1, jobs=1, feature="ha_commands"),
    StableWorkload("pgbench.ha_cluster", "pgbench", "SET CLUSTER ACTIVE/PARTED 并发控制", "pgbench",
                   "ha_cluster.sql", "admin", "console", clients=1, jobs=1, feature="ha_commands"),
    StableWorkload("jdbc.prepared_leak", "jdbc", "PreparedStatement 长短连接缓存稳定性", "jdbc",
                   feature="jdbc"),
)


def enabled_workloads(config):
    """Return workload definitions enabled by the resolved stable configuration."""
    values = config if isinstance(config, dict) else config.values
    selected = values.get("enabled_workloads")
    return tuple(
        item for item in WORKLOADS
        if (selected is None or item.name in selected or item.target in selected)
        and (item.feature is None or values.get(item.feature, {}).get("enabled", True))
    )


def find_workload(name):
    normalized = name[7:] if name.startswith("stable.") else name
    for item in WORKLOADS:
        if item.name == normalized:
            return item
    raise KeyError(name)
