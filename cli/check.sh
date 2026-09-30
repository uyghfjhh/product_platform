#!/usr/bin/env bash
set -euo pipefail
task_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$task_root"
case "${1:-quick}" in
  quick)
    exec .venv/bin/python -m pytest tests/test_review_regressions.py tests/test_platform_v2_capabilities.py tests/test_filestore.py tests/test_regression_engine.py -q --durations=5
    ;;
  full)
    exec .venv/bin/python -m pytest tests/ -q --durations=10
    ;;
  *)
    printf 'Usage: %s [quick|full]\n' "$0" >&2
    exit 2
    ;;
esac
