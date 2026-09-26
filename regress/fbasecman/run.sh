#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# 解释器选择：优先使用 PYTHON_BIN；自动选择时同时检查版本和运行依赖。
if [ -z "${PYTHON_BIN:-}" ]; then
    for candidate in python3.12 python3.11 python3.10 python3.9 python3.8 python3; do
        if command -v "$candidate" >/dev/null 2>&1 &&
           "$candidate" -c 'import sys, yaml; raise SystemExit(0 if sys.version_info >= (3, 8) else 1)' >/dev/null 2>&1; then
            PYTHON_BIN="$candidate"
            break
        fi
    done
fi

# 硬性要求 Python >= 3.8 且安装 PyYAML（requirements.txt）。
if [ -z "${PYTHON_BIN:-}" ] ||
   ! "$PYTHON_BIN" -c 'import sys, yaml; raise SystemExit(0 if sys.version_info >= (3, 8) else 1)' >/dev/null 2>&1; then
    echo "ERROR: No usable Python found (requires Python >= 3.8 and PyYAML)." >&2
    echo "Install dependencies with: <python3.8+> -m pip install -r $ROOT_DIR/requirements.txt" >&2
    echo "Or set PYTHON_BIN to an interpreter with those dependencies installed." >&2
    exit 1
fi

# Web requests use the platform React/FastAPI service, never the former server.
if [ "${1:-}" = "web" ]; then
    shift
    exec "$ROOT_DIR/../../web.sh" "$@"
fi

exec "$PYTHON_BIN" "$ROOT_DIR/tools/cli.py" "$@"
