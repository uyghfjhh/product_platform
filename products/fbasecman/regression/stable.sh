#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# 解释器选择逻辑与 run.sh 一致：PYTHON_BIN 优先，否则探测 3.8+ 可用解释器。
if [ -z "${PYTHON_BIN:-}" ]; then
    for candidate in python3.12 python3.11 python3.10 python3.9 python3.8 python3; do
        if command -v "$candidate" >/dev/null 2>&1; then
            PYTHON_BIN="$candidate"
            break
        fi
    done
fi

"$PYTHON_BIN" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 8) else 1)' 2>/dev/null || {
    echo "ERROR: $PYTHON_BIN is unavailable or older than Python 3.8." >&2
    echo "Set PYTHON_BIN=/path/to/python3.8+ and retry." >&2
    exit 1
}

# Daemonized sanitizer processes cannot reliably keep the invoking terminal's
# stderr. Persist reports per PID so they can be followed from another shell.
ASAN_LOG_DIR="$ROOT_DIR/output/stable/asan"
mkdir -p "$ASAN_LOG_DIR"
export ASAN_OPTIONS="${ASAN_OPTIONS:-abort_on_error=1:disable_coredump=0:detect_leaks=0:log_path=$ASAN_LOG_DIR/fbasecman}"

exec "$PYTHON_BIN" "$ROOT_DIR/tools/stable_cli.py" "$@"
