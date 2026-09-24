#!/usr/bin/env bash
# This pgbench build logs every protocol message even without -r.  Preserve
# errors, progress, and the final result while keeping a 40-minute run usable.
set -euo pipefail

set +e
"$@" 2>&1 | awk '
  /^pgbench \(/ || /^pgbench: pghost:/ || /^pgbench: error:/ || /^pgbench: hint:/ ||
  /^progress:/ || /^transaction type:/ || /^scaling factor:/ || /^query mode:/ ||
  /^number of clients:/ || /^number of threads:/ || /^maximum number of tries:/ ||
  /^duration:/ || /^number of transactions actually processed:/ ||
  /^number of failed transactions:/ || /^latency average =/ || /^latency stddev =/ ||
  /^initial connection time =/ || /^average connection time =/ || /^tps =/ { print; fflush(); }
'
status=${PIPESTATUS[0]}
set -e
exit "$status"
