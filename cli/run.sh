#!/usr/bin/env bash
# 零配置回归入口：run/show/doctor/clean/pack/reset
set -euo pipefail
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="$REPO_DIR/.venv/bin/python"
if [[ ! -x "$PYTHON_BIN" ]]; then
    for candidate in python3.12 python3.11 python3; do
        if command -v "$candidate" >/dev/null 2>&1 && \
           "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,12) else 1)'; then
            PYTHON_BIN="$candidate"; break
        fi
    done
fi
export PYTHONPATH="$REPO_DIR/backend:$REPO_DIR${PYTHONPATH:+:$PYTHONPATH}"
exec "$PYTHON_BIN" "$REPO_DIR/cli/platform_cli.py" "$@"
