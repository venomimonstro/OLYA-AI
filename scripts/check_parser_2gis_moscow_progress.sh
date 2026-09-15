#!/usr/bin/env bash
set -uo pipefail
cd "$(dirname "$0")/.."

ROOT="data/2gis/moscow_bulk"
CHUNKS="$ROOT/chunks"
RESULTS="$ROOT/results"

human_bytes() {
  local bytes="${1:-0}"
  python3 - "$bytes" <<'PY'
import sys
n=float(sys.argv[1] or 0)
for unit in ('B','KB','MB','GB','TB'):
    if n < 1024 or unit == 'TB':
        print(f"{n:.1f}{unit}" if unit != 'B' else f"{int(n)}B")
        break
    n /= 1024
PY
}

total=$(find "$CHUNKS" -maxdepth 1 -type f -name '*.urls' 2>/dev/null | wc -l | tr -d ' ')
done_count=$(find "$RESULTS" -maxdepth 1 -type f -name '*.done' 2>/dev/null | wc -l | tr -d ' ')
json_count=$(find "$RESULTS" -maxdepth 1 -type f -name '*.json' -size +2c 2>/dev/null | wc -l | tr -d ' ')
partial=$((json_count - done_count))
if (( partial < 0 )); then partial=0; fi
bytes=$(find "$RESULTS" -maxdepth 1 -type f -name '*.json' -printf '%s\n' 2>/dev/null | awk '{s+=$1} END{print s+0}')
percent=$(python3 - "$done_count" "$total" <<'PY'
import sys
done=int(sys.argv[1] or 0); total=int(sys.argv[2] or 0)
print(f"{(done*100/total):.1f}" if total else "0.0")
PY
)

active=$(docker ps --filter label=com.docker.compose.service=parser-2gis --format '{{.Names}}' 2>/dev/null | wc -l | tr -d ' ')

echo "=== 2GIS Moscow live progress ==="
echo "overall: ${done_count}/${total} chunks done (${percent}%)"
echo "active_workers: $active"
echo "chunks_with_json: $json_count"
echo "chunks_partial_or_retry: $partial"
echo "raw_json_size: $(human_bytes "$bytes")"

# Show every currently active/partial chunk and the number of URLs completed
# according to parser-2gis' own per-URL completion log.
shown=0
for log in "$RESULTS"/*.log; do
  [[ -e "$log" ]] || continue
  base=$(basename "$log" .log)
  [[ -f "$RESULTS/$base.done" ]] && continue
  urlfile=$(find "$CHUNKS" -maxdepth 1 -type f -name "$base.urls" -print -quit 2>/dev/null)
  expected=0
  [[ -n "$urlfile" ]] && expected=$(grep -cve '^\s*$' "$urlfile" 2>/dev/null || true)
  started=$(grep -c "Парсинг ссылки http" "$log" 2>/dev/null || true)
  completed=$(grep -c "Парсинг ссылки завершён" "$log" 2>/dev/null || true)
  errors=$(grep -c "Ошибка во время работы парсера" "$log" 2>/dev/null || true)
  outfile="$RESULTS/$base.json"
  outbytes=0
  [[ -f "$outfile" ]] && outbytes=$(stat -c %s "$outfile" 2>/dev/null || echo 0)
  if (( started > 0 || outbytes > 0 )); then
    echo "  $base: urls ${completed}/${expected:-0}, started=$started, errors=$errors, json=$(human_bytes "$outbytes")"
    shown=$((shown + 1))
  fi
done
if (( shown == 0 )) && (( active > 0 )); then
  echo "  workers are starting Chromium; no rubric has completed yet"
fi

if docker compose ps --status running app 2>/dev/null | grep -q app; then
  echo "--- owned index ---"
  docker compose exec -T -e PYTHONPATH=/app app python /app/scripts/check_2gis_moscow_coverage.py || true
else
  echo "app container is not running; database coverage check skipped"
fi
