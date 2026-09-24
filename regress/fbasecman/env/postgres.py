from textwrap import dedent

from framework.configuration import RegressionConfig

from framework.execution.shell import LoggedShellRunner
from .topology import standby_ports


def _mmr_nodes(env: RegressionConfig):
    db = env.config["database"]
    base = db.get("mmr_data_root", db["mmr_postgres_dir"])
    nodes = [("test_mmr1", f"{base}/test_mmr1"),
             ("test_mmr2", f"{base}/test_mmr2")]
    for cluster in ("mmr1", "mmr2"):
        nodes.extend((f"test_{cluster}_s{index}", f"{base}/test_{cluster}_s{index}")
                     for index, _ in enumerate(standby_ports(db, cluster), 1))
    return nodes


def _rep_nodes(env: RegressionConfig):
    return []




def _build_pg_ctl_script(pg_dir: str, nodes, action: str) -> str:
    if action not in ("start", "restart", "stop"):
        raise ValueError(f"unsupported action: {action}")

    lines = [
        f'PG_DIR="{pg_dir}"',
        f'ACTION="{action}"',
        "",
        "report_start_failure() {",
        '    name="$1"',
        '    pgdata="$2"',
        '    echo "[env] PostgreSQL failed to start: node=$name pgdata=$pgdata" >&2',
        '    if [ -s "$pgdata/current_logfiles" ]; then',
        '        while read -r destination log_path; do',
        '            [ -n "$log_path" ] || continue',
        '            case "$log_path" in /*) ;; *) log_path="$pgdata/$log_path" ;; esac',
        '            if [ -f "$log_path" ]; then',
        '                echo "[env] PostgreSQL log tail: $log_path" >&2',
        '                tail -n 40 "$log_path" >&2',
        '            fi',
        '        done < "$pgdata/current_logfiles"',
        '    elif [ -f "$pgdata/logfile" ]; then',
        '        echo "[env] pg_ctl log tail: $pgdata/logfile" >&2',
        '        tail -n 40 "$pgdata/logfile" >&2',
        '    fi',
        '    port="$(sed -n \'s/^[[:space:]]*port[[:space:]]*=[[:space:]]*\\([0-9][0-9]*\\).*/\\1/p\' "$pgdata/postgresql.conf" | tail -n 1)"',
        '    if [ -n "$port" ]; then',
        '        listeners="$(ss -ltnp 2>/dev/null | awk -v endpoint=":$port" \'index($4, endpoint) { print }\')"',
        '        if [ -n "$listeners" ]; then',
        '            echo "[env] port $port is already in use:" >&2',
        '            echo "$listeners" >&2',
        '        fi',
        '    fi',
        "}",
        "",
        "start_node() {",
        '    name="$1"',
        '    pgdata="$2"',
        '    $PG_DIR/bin/pg_ctl -D "$pgdata" -l "$pgdata/logfile" start',
        '    rc="$?"',
        '    if [ "$rc" -ne 0 ]; then',
        '        report_start_failure "$name" "$pgdata"',
        "    fi",
        '    return "$rc"',
        "}",
        "",
        "run_node() {",
        '    name="$1"',
        '    pgdata="$2"',
        '    if [ ! -d "$pgdata" ]; then',
        '        if [ "$ACTION" = "stop" ]; then',
        '            echo "[env] already absent: $name -> $pgdata"',
        "            return 0",
        "        fi",
        '        echo "[env] missing pgdata: $name -> $pgdata" >&2',
        "        return 10",
        "    fi",
        '    echo "[env] $ACTION $name: $pgdata"',
        '    if [ "$ACTION" = "start" ]; then',
        '        status="$($PG_DIR/bin/pg_ctl -D "$pgdata" status 2>&1 || true)"',
        '        case "$status" in',
        '            *"server is running"*)',
        '                echo "[env] already running: $name"',
        "                return 0",
        "                ;;",
        "        esac",
        '        start_node "$name" "$pgdata"',
        '    elif [ "$ACTION" = "stop" ]; then',
        '        status="$($PG_DIR/bin/pg_ctl -D "$pgdata" status 2>&1 || true)"',
        '        case "$status" in',
        '            *"server is running"*)',
        '                $PG_DIR/bin/pg_ctl -D "$pgdata" -l "$pgdata/logfile" -m immediate stop',
        "                ;;",
        "            *)",
        '                echo "[env] already stopped: $name"',
        "                ;;",
        "        esac",
        "    else",
        '        status="$($PG_DIR/bin/pg_ctl -D "$pgdata" status 2>&1 || true)"',
        '        case "$status" in',
        '            *"server is running"*)',
        '                $PG_DIR/bin/pg_ctl -D "$pgdata" -l "$pgdata/logfile" restart',
        '                rc="$?"',
        '                if [ "$rc" -ne 0 ]; then',
        '                    report_start_failure "$name" "$pgdata"',
        '                    return "$rc"',
        '                fi',
        "                ;;",
        "            *)",
        '                echo "[env] restart fallback to start: $name"',
        '                start_node "$name" "$pgdata"',
        "                ;;",
        "        esac",
        "    fi",
        "}",
        "",
    ]
    for name, pgdata in nodes:
        lines.append(f'run_node "{name}" "{pgdata}"')
    return "\n".join(lines)


def _postgres_batch_timeout(node_count, default_timeout):
    """Allow each serialized pg_ctl start a bounded amount of time."""
    # Slow hosts can spend tens of seconds initializing one standby.  Reserve
    # 30 seconds per node while retaining the configured timeout as a floor.
    return max(float(default_timeout), max(1, int(node_count)) * 30.0)


def start_environment_postgres(env: RegressionConfig, runner: LoggedShellRunner) -> None:
    db = env.config["database"]
    mmr_nodes = _mmr_nodes(env)
    runner.run_remote(
        db["mmr_pg_user"],
        db["mmr_host"],
        _build_pg_ctl_script(db["mmr_postgres_dir"], mmr_nodes, "start"),
        log_name="03_start_mmr.log",
        timeout=_postgres_batch_timeout(len(mmr_nodes), runner.default_timeout),
    )


def restart_environment_postgres(env: RegressionConfig, runner: LoggedShellRunner) -> None:
    db = env.config["database"]
    mmr_nodes = _mmr_nodes(env)
    runner.run_remote(
        db["mmr_pg_user"],
        db["mmr_host"],
        _build_pg_ctl_script(db["mmr_postgres_dir"], mmr_nodes, "restart"),
        log_name="05_restart_mmr.log",
        timeout=_postgres_batch_timeout(len(mmr_nodes), runner.default_timeout),
    )


def stop_environment_postgres(env: RegressionConfig, runner: LoggedShellRunner) -> None:
    db = env.config["database"]
    runner.run_remote(
        db["mmr_pg_user"],
        db["mmr_host"],
        _build_pg_ctl_script(db["mmr_postgres_dir"], _mmr_nodes(env), "stop"),
        log_name="07_stop_mmr.log",
    )
