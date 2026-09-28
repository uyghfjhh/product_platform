#!/usr/bin/env bash
# DEPRECATED manual entry: case execution migrated to the platform
# (web task API / platform_regress.cli). This script is kept only for
# diagnostics commands (env/doctor/show/test); 'run' prints a warning.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$ROOT_DIR/../../../.." && pwd)"
export PYTHONPATH="$REPO_DIR/backend:$REPO_DIR${PYTHONPATH:+:$PYTHONPATH}"

# 解释器选择：优先使用 PYTHON_BIN；自动选择时同时检查版本和运行依赖。
if [ -z "${PYTHON_BIN:-}" ]; then
    for candidate in "$REPO_DIR/.venv/bin/python" python3.12 python3.11 python3.10 python3.9 python3.8 python3; do
        if command -v "$candidate" >/dev/null 2>&1 &&
           "$candidate" -c 'import sys, yaml; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)' >/dev/null 2>&1; then
            PYTHON_BIN="$candidate"
            break
        fi
    done
fi

# 公共平台 SDK 要求 Python >= 3.12，且安装 PyYAML。
if [ -z "${PYTHON_BIN:-}" ] ||
   ! "$PYTHON_BIN" -c 'import sys, yaml; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)' >/dev/null 2>&1; then
    echo "ERROR: No usable Python found (requires Python >= 3.12 and PyYAML)." >&2
    echo "Install dependencies with: <python3.12+> -m pip install -r $ROOT_DIR/requirements.txt" >&2
    echo "Or set PYTHON_BIN to an interpreter with those dependencies installed." >&2
    exit 1
fi

# Web requests use the platform React/FastAPI service, never the former server.
if [ "${1:-}" = "web" ]; then
    shift
    exec "$ROOT_DIR/../../../../web.sh" "$@"
fi

exec "$PYTHON_BIN" "$ROOT_DIR/tools/cli.py" "$@"
