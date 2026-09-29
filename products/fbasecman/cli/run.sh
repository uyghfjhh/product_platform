#!/usr/bin/env bash
set -euo pipefail

PRODUCT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_DIR="$(cd "$PRODUCT_DIR/../.." && pwd)"

if [[ "${1:-}" == "platform-case" ]]; then
    shift
fi
# 与旧 run.sh 一致：web 子命令转发仓库级 web.sh 守护管理
if [[ "${1:-}" == "web" ]]; then
    shift
    exec "$REPO_DIR/web.sh" "$@"
fi
PYTHON_BIN="$REPO_DIR/.venv/bin/python"
if [[ ! -x "$PYTHON_BIN" ]]; then PYTHON_BIN="python3"; fi
export PYTHONPATH="$REPO_DIR/backend:$REPO_DIR${PYTHONPATH:+:$PYTHONPATH}"
exec "$PYTHON_BIN" "$PRODUCT_DIR/cli/platform_case.py" "$@"
