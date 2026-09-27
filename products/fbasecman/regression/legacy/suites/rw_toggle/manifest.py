"""Read/write routing cases migrated from the legacy rw_toggle scenarios."""

from .case import RwToggleCase


def _case(name, summary, topology, route_mode, driver, scenario, notes):
    return RwToggleCase(name, summary, topology, route_mode, driver, scenario, notes)


RW_TOGGLE_CASES = (
    _case("mmr_hint_switch", "MMR HINT 模式读写状态切换", "mmr", "hint", "psql", "switch", (
        "覆盖默认写请求、READ ONLY 请求和 READ WRITE 请求的连续切换。",
        "每次请求记录真实后端端口、地址和 recovery 状态。",
    )),
    _case("mmr_hint_write", "MMR HINT 模式写请求落到 write-leader", "mmr", "hint", "psql", "write", (
        "覆盖隐式写事务和 DML 操作，必须命中当前 MMR write-leader。",
    )),
    _case("mmr_hint_read", "MMR HINT 模式只读请求落到合法读候选", "mmr", "hint", "psql", "read", (
        "覆盖 READ ONLY 请求，允许命中 replica 或非 write-leader。",
    )),
    _case("mmr_hint_jdbc", "MMR HINT 模式 JDBC 读写切换", "mmr", "hint", "jdbc", "jdbc", (
        "JDBC setReadOnly/setAutoCommit 操作与 psql 场景使用同一后端判定。",
    )),
    _case("rep_hint_switch", "REP HINT 模式读写状态切换", "replication", "hint", "psql", "switch", (
        "覆盖 REP 主备读写切换和恢复。",
    )),
    _case("rep_hint_read", "REP HINT 模式只读请求落到主备读候选", "replication", "hint", "psql", "read", (
        "只读请求只能命中 replication primary 或 standby。",
    )),
    _case("rep_hint_write", "REP HINT 模式写请求落到 primary", "replication", "hint", "psql", "write", (
        "写请求必须命中 replication primary。",
    )),
    _case("rep_hint_jdbc", "REP HINT 模式 JDBC 读写切换", "replication", "hint", "jdbc", "jdbc", (
        "JDBC 读写切换覆盖主备路由和连接复用。",
    )),
    _case("rep_read_port", "REP 读端口路由", "replication", "port", "psql", "read", (
        "非 write_port 请求必须命中 REP 可读候选。",
    )),
    _case("rep_write_port", "REP 写端口路由", "replication", "port", "psql", "write", (
        "write_port 请求必须命中 REP primary。",
    )),
    _case("rep_port_jdbc", "REP 端口模式 JDBC 读写路由", "replication", "port", "jdbc", "jdbc", (
        "JDBC 连接分别验证读端口和 write_port。",
    )),
    _case("mmr_read_port", "MMR 读端口路由", "mmr", "port", "psql", "read", (
        "非 write_port 请求必须命中 MMR 合法读候选。",
    )),
    _case("mmr_write_port", "MMR 写端口路由", "mmr", "port", "psql", "write", (
        "write_port 请求必须命中 MMR write-leader。",
    )),
    _case("mmr_port_jdbc", "MMR 端口模式 JDBC 读写路由", "mmr", "port", "jdbc", "jdbc", (
        "JDBC 连接分别验证读端口和 write_port。",
    )),
)


def case_items():
    return list(RW_TOGGLE_CASES)


def find_case(name):
    for case in RW_TOGGLE_CASES:
        if case.name == name:
            return case
    raise KeyError("unknown rw_toggle case: %s" % name)


def validate_manifest():
    names = [case.name for case in RW_TOGGLE_CASES]
    if len(names) != len(set(names)):
        raise ValueError("duplicate rw_toggle case")
    return True
