from textwrap import dedent

from framework.configuration import RegressionConfig

from framework.execution.shell import LoggedShellRunner

from .topology import topology_nodes


def _inventory_script(pg_dir, nodes):
    lines = []
    lines.append('PG_DIR="%s"' % pg_dir)
    lines.append("show_pgdata() {")
    lines.append('    name="$1"')
    lines.append('    port="$2"')
    lines.append('    pgdata="$3"')
    lines.append('    if [ -d "$pgdata" ]; then')
    # pg_ctl uses rc=3 for a stopped instance. Inventory reports that state;
    # it must not turn a read-only status command into a shell failure.
    lines.append('        status="$($PG_DIR/bin/pg_ctl -D "$pgdata" status 2>&1 || true)"')
    lines.append('        case "$status" in')
    lines.append('            *"server is running"*) state="running" ;;')
    lines.append('            *) state="stopped" ;;')
    lines.append("        esac")
    lines.append('        echo "$name|$port|$pgdata|exists|$state"')
    lines.append("    else")
    lines.append('        echo "$name|$port|$pgdata|missing|stopped"')
    lines.append("    fi")
    lines.append("}")
    for node in nodes:
        lines.append('show_pgdata "%s" "%s" "%s"' % node)
    return "\n".join(lines)


def _parse_inventory(stdout):
    rows = []
    for line in stdout.splitlines():
        parts = line.strip().split("|")
        if len(parts) != 5:
            continue
        rows.append(
            {
                "name": parts[0],
                "port": parts[1],
                "pgdata": parts[2],
                "directory": parts[3],
                "status": parts[4],
            }
        )
    return rows


def collect_inventory(env: RegressionConfig, runner: LoggedShellRunner):
    cfg = env.config
    db = cfg["database"]
    ports = db["ports"]
    mmr_dir = db["mmr_postgres_dir"]
    mmr_data_root = db.get("mmr_data_root", mmr_dir)

    configured = topology_nodes(db)
    mmr_nodes = [(name, port, "%s/%s" % (mmr_data_root, name))
                 for name, port in configured["mmr1"] + configured["mmr2"]]

    mmr_result = runner.run_remote(
        db["mmr_pg_user"],
        db["mmr_host"],
        _inventory_script(mmr_dir, mmr_nodes),
        log_name="80_inventory_mmr.log",
    )

    return {
        "mmr_host": db["mmr_host"],
        "mmr": _parse_inventory(mmr_result.stdout),
        "replication": [],
    }


def format_inventory(inventory):
    lines = []
    lines.append("MMR host: %s" % inventory["mmr_host"])
    for row in inventory["mmr"]:
        lines.append(
            "  {name:<12} port={port:<5} dir={directory:<7} status={status:<7} {pgdata}".format(
                **row
            )
        )
    return "\n".join(lines)
