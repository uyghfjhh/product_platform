#!/usr/bin/env bash
set -euo pipefail

# Keep the stable-test CLI available from the product package during migration.
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../regression" && pwd)"
exec "$ROOT_DIR/stable.sh" "$@"
