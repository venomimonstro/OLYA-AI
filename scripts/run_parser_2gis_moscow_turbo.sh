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

export PARSER_2GIS_WORKERS="${PARSER_2GIS_WORKERS:-4}"
export PARSER_2GIS_CHUNK_SIZE="${PARSER_2GIS_CHUNK_SIZE:-24}"
export PARSER_2GIS_MAX_RECORDS="${PARSER_2GIS_MAX_RECORDS:-50000}"
export PARSER_2GIS_CHROME_MEMORY_MB="${PARSER_2GIS_CHROME_MEMORY_MB:-700}"
export PARSER_2GIS_CONTAINER_MEMORY_MB="${PARSER_2GIS_CONTAINER_MEMORY_MB:-1050}"
export PARSER_2GIS_DELAY_MS="${PARSER_2GIS_DELAY_MS:-0}"

bash scripts/run_parser_2gis_moscow_bulk.sh
