#!/usr/bin/env bash
set -uo pipefail
cd "$(dirname "$0")/.."

ROOT="data/2gis/moscow_bulk"
CHUNKS="$ROOT/chunks"
RESULTS="$ROOT/results"

total=$(find "$CHUNKS" -maxdepth 1 -type f -name '*.urls' 2>/dev/null | wc -l | tr -d ' ')
done_count=$(find "$RESULTS" -maxdepth 1 -type f -name '*.done' 2>/dev/null | wc -l | tr -d ' ')
json_count=$(find "$RESULTS" -maxdepth 1 -type f -name '*.json' -size +2c 2>/dev/null | wc -l | tr -d ' ')
partial=$((json_count - done_count))
if (( partial < 0 )); then partial=0; fi
size=$(du -sh "$RESULTS" 2>/dev/null | awk '{print $1}')
size=${size:-0}

echo "chunks_total: $total"
echo "chunks_done: $done_count"
echo "chunks_with_json: $json_count"
echo "chunks_partial_or_retry: $partial"
echo "raw_results_size: $size"

if docker compose ps --status running app 2>/dev/null | grep -q app; then
  echo "--- owned index ---"
  docker compose exec -T -e PYTHONPATH=/app app python /app/scripts/check_2gis_moscow_coverage.py || true
else
  echo "app container is not running; database coverage check skipped"
fi
