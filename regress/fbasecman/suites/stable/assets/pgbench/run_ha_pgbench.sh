#!/usr/bin/env bash
# 持续执行高可用命令；配置锁竞争属于预期结果，其他错误立即失败。
set -u

duration=$1
shift
deadline=$(( $(date +%s) + duration ))
attempts=0
successes=0
reload_busy=0
refresh_retry=0
output=$(mktemp)
trap 'rm -f "$output"' EXIT

while [ "$(date +%s)" -lt "$deadline" ]; do
	attempts=$((attempts + 1))
	"$@" >"$output" 2>&1
	status=$?
	if [ "$status" -eq 0 ]; then
		successes=$((successes + 1))
		# Keep management commands observable while allowing the previous
		# persistence/reload cycle and monitor publication to settle.
		sleep 5
		continue
	fi
	if grep -q 'RELOAD BUSY: another reload or configuration persistence command is running; try again' "$output" &&
	   ! grep -Eiq 'FATAL:|PANIC:|server closed|connection refused|backend died' "$output"; then
		reload_busy=$((reload_busy + 1))
		sleep 2
		continue
	fi
	if grep -Eq 'REFRESH CLUSTER "[^"]+" could not be scheduled' "$output" &&
	   ! grep -Eiq 'FATAL:|PANIC:|server closed|connection refused|backend died' "$output"; then
		refresh_retry=$((refresh_retry + 1))
		sleep 2
		continue
	fi
	cat "$output"
	echo "HA_RETRY_SUMMARY attempts=$attempts successes=$successes reload_busy=$reload_busy refresh_retry=$refresh_retry unexpected_failures=1"
	exit "$status"
done

echo "HA_RETRY_SUMMARY attempts=$attempts successes=$successes reload_busy=$reload_busy refresh_retry=$refresh_retry unexpected_failures=0"
[ "$successes" -gt 0 ]
