#!/usr/bin/env bash
set -euo pipefail

PRODUCT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_DIR="$(cd "$PRODUCT_DIR/../.." && pwd)"

if [[ "${1:-}" == "platform-case" ]]; then
    shift
    PYTHON_BIN="$REPO_DIR/.venv/bin/python"
    if [[ ! -x "$PYTHON_BIN" ]]; then PYTHON_BIN="python3"; fi
    export PYTHONPATH="$REPO_DIR/backend:$REPO_DIR${PYTHONPATH:+:$PYTHONPATH}"
    exec "$PYTHON_BIN" "$PRODUCT_DIR/cli/platform_case.py" "$@"
fi

# The remaining suite and aggregate commands keep their original behavior.
exec "$PRODUCT_DIR/regression/legacy/run.sh" "$@"
