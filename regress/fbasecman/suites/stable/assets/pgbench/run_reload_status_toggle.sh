#!/usr/bin/env bash
# Toggle one datasource in the live config and RELOAD after every change.
set -uo pipefail

usage() {
	cat <<'EOF'
usage: run_reload_status_toggle.sh DURATION INTERVAL CONFIG DATASOURCE PSQL HOST PORT

Example:
  run_reload_status_toggle.sh 300 0.2 output/stable/current/config/fbasecman.conf \
    pg_240 /usr/local/pgsql15.3-mmr/bin/psql 127.0.0.1 26432

DURATION=0 runs until interrupted. The console user/database are admin/console.
EOF
}

if [ "$#" -ne 7 ]; then
	usage >&2
	exit 2
fi

duration=$1
interval=$2
config=$3
datasource=$4
psql_bin=$5
host=$6
port=$7

case "$duration" in
	''|*[!0-9]*) echo "DURATION must be a non-negative integer" >&2; exit 2 ;;
esac
if ! [[ "$interval" =~ ^[0-9]+([.][0-9]+)?$ ]]; then
	echo "INTERVAL must be a non-negative number" >&2
	exit 2
fi
if [ ! -f "$config" ]; then
	echo "config not found: $config" >&2
	exit 2
fi
if [ ! -x "$psql_bin" ]; then
	echo "psql is not executable: $psql_bin" >&2
	exit 2
fi

set_status() {
	python3 - "$config" "$datasource" "$1" <<'PY'
import os
import re
import stat
import sys
import tempfile

path, datasource, status = sys.argv[1:]
with open(path, "r") as handle:
    content = handle.read()

block_re = re.compile(
    r'(datasources\s+"%s"\s*\{)(.*?)(^\})' % re.escape(datasource),
    re.MULTILINE | re.DOTALL,
)
matches = list(block_re.finditer(content))
if len(matches) != 1:
    raise SystemExit("expected exactly one datasource %r, found %d" %
                     (datasource, len(matches)))

match = matches[0]
body = match.group(2)
status_re = re.compile(r'(?m)^([ \t]*status[ \t]+)"(?:active|parted)"([ \t]*(?:#.*)?)$')
status_matches = list(status_re.finditer(body))
if len(status_matches) != 1:
    raise SystemExit("expected exactly one active/parted status in datasource %r, found %d" %
                     (datasource, len(status_matches)))

new_body = status_re.sub(r'\1"%s"\2' % status, body, count=1)
updated = content[:match.start()] + match.group(1) + new_body + match.group(3) + content[match.end():]
file_stat = os.stat(path)
fd, temporary = tempfile.mkstemp(prefix=".%s." % os.path.basename(path), dir=os.path.dirname(path) or ".")
try:
    with os.fdopen(fd, "w") as handle:
        handle.write(updated)
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(temporary, stat.S_IMODE(file_stat.st_mode))
    os.replace(temporary, path)
finally:
    if os.path.exists(temporary):
        os.unlink(temporary)
PY
}

reload_config() {
	local output status
	output=$("$psql_bin" -X -w -h "$host" -p "$port" -U admin -d console \
		-v ON_ERROR_STOP=1 -Atqc 'RELOAD;' 2>&1)
	status=$?
	# The console accepts RELOAD as a command but returns no result row.  psql's
	# exit status is therefore the authoritative success signal.
	if [ "$status" -ne 0 ]; then
		echo "RELOAD failed (rc=$status): $output" >&2
		return 1
	fi
}

original_status=$(python3 - "$config" "$datasource" <<'PY'
import re
import sys

content = open(sys.argv[1], "r").read()
block = re.findall(r'datasources\s+"%s"\s*\{(.*?)^\}' % re.escape(sys.argv[2]),
                   content, re.MULTILINE | re.DOTALL)
if len(block) != 1:
    raise SystemExit("expected exactly one datasource %r, found %d" % (sys.argv[2], len(block)))
status = re.findall(r'(?m)^[ \t]*status[ \t]+"(active|parted)"', block[0])
if len(status) != 1:
    raise SystemExit("datasource %r must have exactly one active/parted status" % sys.argv[2])
print(status[0])
PY
) || exit 1

restored=0
restore_config() {
	if [ "$restored" -eq 1 ]; then
		return
	fi
	restored=1
	set_status "$original_status" || return
	# A crashed proxy cannot accept RELOAD, but its on-disk config is still restored.
	reload_config || true
}
trap restore_config EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP

started=$(date +%s)
deadline=$((started + duration))
cycles=0
reloads=0

while [ "$duration" -eq 0 ] || [ "$(date +%s)" -lt "$deadline" ]; do
	for next_status in active parted; do
		set_status "$next_status" || exit 1
		reload_config || exit 1
		reloads=$((reloads + 1))
		if [ "$next_status" = parted ]; then
			cycles=$((cycles + 1))
		fi
		echo "$(date '+%F %T') datasource=$datasource status=$next_status reload=$reloads"
		sleep "$interval"
		if [ "$duration" -ne 0 ] && [ "$(date +%s)" -ge "$deadline" ]; then
			break 2
		fi
	done
done

echo "RELOAD_STATUS_SUMMARY datasource=$datasource cycles=$cycles reloads=$reloads original_status=$original_status"
