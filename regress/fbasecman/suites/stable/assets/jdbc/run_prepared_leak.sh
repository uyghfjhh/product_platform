#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)"
SCENARIO_DIR="$ROOT_DIR/suites/stable/assets/jdbc"
BUILD_DIR="$SCENARIO_DIR/build"
JDBC_DIR="${JDBC_DIR:-$ROOT_DIR/lib_jdbc}"

WORKLOAD="prepared_leak"
RUN_DIR=""
DURATION="7200"
LONG_CLIENTS="10"
SHORT_CLIENTS="4"
SHARED_SQL_COUNT="2000"
PRIVATE_SQL_COUNT="0"
SHORT_BATCH="10"
SHORT_IDLE_MS="0"
RW_SWITCH_ENABLED="false"
RW_SWITCH_INTERVAL_OPS="0"
HEARTBEAT_ENABLED="false"
HEARTBEAT_INTERVAL_OPS="0"
GUC_ENABLED="false"
GUC_INTERVAL_OPS="0"
GUC_DISTINCT_COUNT="200"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --workload)
      WORKLOAD="$2"
      shift 2
      ;;
    --run-dir)
      RUN_DIR="$2"
      shift 2
      ;;
    --duration)
      DURATION="$2"
      shift 2
      ;;
    --long-clients)
      LONG_CLIENTS="$2"
      shift 2
      ;;
    --short-clients)
      SHORT_CLIENTS="$2"
      shift 2
      ;;
    --shared-sql-count)
      SHARED_SQL_COUNT="$2"
      shift 2
      ;;
    --private-sql-count)
      PRIVATE_SQL_COUNT="$2"
      shift 2
      ;;
    --short-batch)
      SHORT_BATCH="$2"
      shift 2
      ;;
    --short-idle-ms)
      SHORT_IDLE_MS="$2"
      shift 2
      ;;
    --rw-switch-enabled)
      RW_SWITCH_ENABLED="$2"
      shift 2
      ;;
    --rw-switch-interval-ops)
      RW_SWITCH_INTERVAL_OPS="$2"
      shift 2
      ;;
    --heartbeat-enabled)
      HEARTBEAT_ENABLED="$2"
      shift 2
      ;;
    --heartbeat-interval-ops)
      HEARTBEAT_INTERVAL_OPS="$2"
      shift 2
      ;;
    --guc-enabled)
      GUC_ENABLED="$2"
      shift 2
      ;;
    --guc-interval-ops)
      GUC_INTERVAL_OPS="$2"
      shift 2
      ;;
    --guc-distinct-count)
      GUC_DISTINCT_COUNT="$2"
      shift 2
      ;;
    *)
      echo "unknown argument: $1" >&2
      exit 1
      ;;
  esac
done

mkdir -p "$BUILD_DIR"

JAR_PATH="$(find "$JDBC_DIR" -maxdepth 1 -name 'postgresql-*.jar' | sort | tail -n 1)"
if [[ -z "${JAR_PATH:-}" ]]; then
  echo "jdbc jar not found under $JDBC_DIR" >&2
  exit 1
fi

javac -cp "$JAR_PATH" -d "$BUILD_DIR" "$SCENARIO_DIR/PreparedLeakMain.java"

exec java -Dfbasecman.stable.run_dir="$RUN_DIR" -cp "$BUILD_DIR:$JAR_PATH" PreparedLeakMain \
  --workload "$WORKLOAD" \
  --duration "$DURATION" \
  --long-clients "$LONG_CLIENTS" \
  --short-clients "$SHORT_CLIENTS" \
  --shared-sql-count "$SHARED_SQL_COUNT" \
  --private-sql-count "$PRIVATE_SQL_COUNT" \
  --short-batch "$SHORT_BATCH" \
  --short-idle-ms "$SHORT_IDLE_MS" \
  --rw-switch-enabled "$RW_SWITCH_ENABLED" \
  --rw-switch-interval-ops "$RW_SWITCH_INTERVAL_OPS" \
  --heartbeat-enabled "$HEARTBEAT_ENABLED" \
  --heartbeat-interval-ops "$HEARTBEAT_INTERVAL_OPS" \
  --guc-enabled "$GUC_ENABLED" \
  --guc-interval-ops "$GUC_INTERVAL_OPS" \
  --guc-distinct-count "$GUC_DISTINCT_COUNT"
