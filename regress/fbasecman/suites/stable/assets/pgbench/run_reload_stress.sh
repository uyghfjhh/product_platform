#!/usr/bin/env bash
# Run config status/reload churn concurrently with the read/write session workload.
set -uo pipefail

if [ "$#" -lt 9 ]; then
	echo "usage: run_reload_stress.sh DURATION INTERVAL CONFIG DATASOURCE PSQL HOST PORT PGBENCH..." >&2
	exit 2
fi

duration=$1
interval=$2
config=$3
datasource=$4
psql_bin=$5
host=$6
port=$7
shift 7

asset_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
toggle_pid=0
cleanup() {
	if [ "$toggle_pid" -gt 0 ] && kill -0 "$toggle_pid" 2>/dev/null; then
		kill -TERM "$toggle_pid" 2>/dev/null || true
		wait "$toggle_pid" 2>/dev/null || true
	fi
}
trap cleanup EXIT INT TERM HUP

bash "$asset_dir/run_reload_status_toggle.sh" \
	"$duration" "$interval" "$config" "$datasource" "$psql_bin" "$host" "$port" &
toggle_pid=$!

bash "$asset_dir/run_pgbench.sh" "$@"
pgbench_status=$?

wait "$toggle_pid"
toggle_status=$?
toggle_pid=0

if [ "$pgbench_status" -ne 0 ]; then
	echo "reload stress pgbench failed: rc=$pgbench_status" >&2
	exit "$pgbench_status"
fi
if [ "$toggle_status" -ne 0 ]; then
	echo "reload status toggle failed: rc=$toggle_status" >&2
	exit "$toggle_status"
fi
