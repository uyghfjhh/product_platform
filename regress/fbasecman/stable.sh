#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"

# Daemonized sanitizer processes cannot reliably keep the invoking terminal's
# stderr. Persist reports per PID so they can be followed from another shell.
ASAN_LOG_DIR="$ROOT_DIR/output/stable/asan"
mkdir -p "$ASAN_LOG_DIR"
export ASAN_OPTIONS="${ASAN_OPTIONS:-abort_on_error=1:disable_coredump=0:detect_leaks=0:log_path=$ASAN_LOG_DIR/fbasecman}"

exec "$PYTHON_BIN" "$ROOT_DIR/tools/stable_cli.py" "$@"
