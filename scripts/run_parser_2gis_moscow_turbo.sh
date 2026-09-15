#!/usr/bin/env bash
set -uo pipefail

cd "$(dirname "$0")/.."

restore_llama() {
  echo "[turbo] Restoring llama service..."
  docker compose start llama >/dev/null 2>&1 || true
}
trap restore_llama EXIT INT TERM

echo "[turbo] Stopping llama temporarily to free RAM for parallel Chromium workers..."
docker compose stop llama

# One rubric per job is intentionally used here. parser-2gis 1.2.1 aborts a
# multi-URL batch when one rubric fails, so s001 jobs give deterministic resume,
# exact progress and isolate failures. Parallel workers recover the startup cost.
export PARSER_2GIS_WORKERS="${PARSER_2GIS_WORKERS:-6}"
export PARSER_2GIS_CHUNK_SIZE="${PARSER_2GIS_CHUNK_SIZE:-1}"
export PARSER_2GIS_MAX_RECORDS="${PARSER_2GIS_MAX_RECORDS:-50000}"
export PARSER_2GIS_CHROME_MEMORY_MB="${PARSER_2GIS_CHROME_MEMORY_MB:-650}"
export PARSER_2GIS_CONTAINER_MEMORY_MB="${PARSER_2GIS_CONTAINER_MEMORY_MB:-850}"
export PARSER_2GIS_DELAY_MS="${PARSER_2GIS_DELAY_MS:-0}"
export PARSER_2GIS_PROGRESS_INTERVAL="${PARSER_2GIS_PROGRESS_INTERVAL:-10}"

bash scripts/run_parser_2gis_moscow_bulk.sh
