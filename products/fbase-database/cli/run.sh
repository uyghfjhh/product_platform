#!/usr/bin/env bash
set -euo pipefail

PRODUCT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_DIR="$(cd "$PRODUCT_DIR/../.." && pwd)"

if [[ "${1:-}" == "platform-case" ]]; then
    shift
    PYTHON_BIN="$REPO_DIR/.venv/bin/python"
    if [[ ! -x "$PYTHON_BIN" ]]; then PYTHON_BIN="python3"; fi
    export PYTHONPATH="$REPO_DIR/backend:$REPO_DIR${PYTHONPATH:+:$PYTHONPATH}"
    exec "$PYTHON_BIN" -m platform_regress.cli --product-dir "$PRODUCT_DIR" "$@"
fi

# Legacy targets remain callable until their business cases move to the SDK.
exec "$PRODUCT_DIR/regression/legacy/run.sh" "$@"
