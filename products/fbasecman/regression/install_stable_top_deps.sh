#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3.8}"

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "ERROR: cannot find $PYTHON_BIN; Textual TUI requires Python 3.8 or newer."
  exit 1
fi

"$PYTHON_BIN" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 8) else 1)' || {
  echo "ERROR: $PYTHON_BIN is older than Python 3.8."
  exit 1
}

"$PYTHON_BIN" -m pip install --user -r "$ROOT_DIR/requirements-stable-top.txt"
echo "stable Textual TUI dependencies installed for $PYTHON_BIN"
