#!/usr/bin/env bash
set -uo pipefail

cd "$(dirname "$0")/.."

WORKERS="${PARSER_2GIS_WORKERS:-2}"
CHUNK_SIZE="${PARSER_2GIS_CHUNK_SIZE:-18}"
MAX_RECORDS="${PARSER_2GIS_MAX_RECORDS:-50000}"
CHROME_MEMORY="${PARSER_2GIS_CHROME_MEMORY_MB:-650}"
CONTAINER_MEMORY="${PARSER_2GIS_CONTAINER_MEMORY_MB:-900}"
DELAY_MS="${PARSER_2GIS_DELAY_MS:-0}"
PROGRESS_INTERVAL="${PARSER_2GIS_PROGRESS_INTERVAL:-15}"

ROOT="data/2gis/moscow_bulk"
CHUNKS="$ROOT/chunks"
RESULTS="$ROOT/results"
RUBRICS="$ROOT/rubrics.tsv"
IMPORT_LOCK="$ROOT/import.lock"
mkdir -p "$CHUNKS" "$RESULTS"

progress_snapshot() {
  local total done json_count active percent bytes
  total=$(find "$CHUNKS" -maxdepth 1 -type f -name '*.urls' 2>/dev/null | wc -l | tr -d ' ')
  done=$(find "$RESULTS" -maxdepth 1 -type f -name '*.done' 2>/dev/null | wc -l | tr -d ' ')
  json_count=$(find "$RESULTS" -maxdepth 1 -type f -name '*.json' -size +2c 2>/dev/null | wc -l | tr -d ' ')
  active=$(docker ps --filter label=com.docker.compose.service=parser-2gis --format '{{.Names}}' 2>/dev/null | wc -l | tr -d ' ')
  bytes=$(find "$RESULTS" -maxdepth 1 -type f -name '*.json' -printf '%s\n' 2>/dev/null | awk '{s+=$1} END{print s+0}')
  percent=$(python3 - "$done" "$total" <<'PY'
import sys
done=int(sys.argv[1] or 0); total=int(sys.argv[2] or 0)
print(f"{(done*100/total):.1f}" if total else "0.0")
PY
)
  echo "[progress] chunks=${done}/${total} (${percent}%) active_workers=${active} json_files=${json_count} raw_mb=$((bytes/1024/1024))"

  local log base expected completed errors outbytes
  for log in "$RESULTS"/*.log; do
    [[ -e "$log" ]] || continue
    base=$(basename "$log" .log)
    [[ -f "$RESULTS/$base.done" ]] && continue
    expected=$(grep -cve '^\s*$' "$CHUNKS/$base.urls" 2>/dev/null || true)
    completed=$(grep -c "Парсинг ссылки завершён" "$log" 2>/dev/null || true)
    errors=$(grep -c "Ошибка во время работы парсера" "$log" 2>/dev/null || true)
    outbytes=0
    [[ -f "$RESULTS/$base.json" ]] && outbytes=$(stat -c %s "$RESULTS/$base.json" 2>/dev/null || echo 0)
    if (( completed > 0 || outbytes > 0 )); then
      echo "           $base urls=${completed}/${expected:-0} errors=${errors} json_mb=$((outbytes/1024/1024))"
    fi
  done
}

monitor_progress() {
  while true; do
    sleep "$PROGRESS_INTERVAL"
    progress_snapshot
  done
}

incremental_import() {
  local outfile="$1"
  local base="$2"
  [[ -s "$outfile" ]] || return 0
  local import_log="$RESULTS/${base}.import.log"
  local lock_fd
  # SQLite supports concurrent readers, but serialize the four bulk writers so
  # the LLM/app never sees avoidable SQLITE_BUSY spikes during a turbo run.
  exec {lock_fd}>"$IMPORT_LOCK"
  flock "$lock_fd"
  if docker compose exec -T -e PYTHONPATH=/app app \
      python /app/scripts/import_2gis_json.py "/app/data/2gis/moscow_bulk/results/${base}.json" --city "Москва" \
      >"$import_log" 2>&1; then
    python3 - "$import_log" "$base" <<'PY'
import json, sys
from pathlib import Path
p=Path(sys.argv[1]); base=sys.argv[2]
try:
    d=json.loads(p.read_text(encoding='utf-8'))
    print(f"[import] {base}: written={d.get('written',0)} total_businesses={(d.get('store') or {}).get('businesses','?')} ratings={d.get('ratings_imported',0)}")
except Exception:
    print(f"[import] {base}: completed")
PY
  else
    echo "[import-warning] $base import failed; final bulk import will retry it"
  fi
  flock -u "$lock_fd"
  exec {lock_fd}>&-
}

echo "[1/5] Building parser image..."
docker compose -f docker-compose.2gis.yml --profile 2gis build parser-2gis

echo "[2/5] Extracting Russian leaf rubrics bundled with parser-2gis..."
X1_2GIS_PARSER_MEMORY_LIMIT_MB="$CONTAINER_MEMORY" \
  docker compose -f docker-compose.2gis.yml --profile 2gis run --rm --entrypoint python parser-2gis -c '
import json
from pathlib import Path
import parser_2gis
root = Path(parser_2gis.__file__).resolve().parent
rubrics = json.loads((root / "data" / "rubrics.json").read_text(encoding="utf-8"))
rows = []
for row in rubrics.values():
    if not isinstance(row, dict) or not row.get("isRussian") or row.get("children"):
        continue
    code = str(row.get("code") or "").strip()
    label = " ".join(str(row.get("label") or "").split()).strip()
    if code and label:
        rows.append((int(code) if code.isdigit() else 10**12, code, label))
rows.sort()
out = Path("/data/moscow_bulk/rubrics.tsv")
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text("".join(f"{code}\t{label}\n" for _, code, label in rows), encoding="utf-8")
print(f"leaf_rubrics={len(rows)}")
'

rm -f "$CHUNKS"/*.urls 2>/dev/null || true
python3 - "$RUBRICS" "$CHUNKS" "$CHUNK_SIZE" <<'PY'
import sys
from pathlib import Path
from urllib.parse import quote

rubrics = Path(sys.argv[1])
chunks = Path(sys.argv[2])
size = max(1, int(sys.argv[3]))
rows = []
for line in rubrics.read_text(encoding="utf-8").splitlines():
    code, label = line.split("\t", 1)
    rows.append((code.strip(), label.strip()))
for start in range(0, len(rows), size):
    batch = rows[start:start + size]
    path = chunks / f"chunk_s{size:03d}_{start // size:04d}.urls"
    urls = [
        f"https://2gis.ru/moscow/search/{quote(label, safe='')}/rubricId/{code}/filters/sort=name"
        for code, label in batch
    ]
    path.write_text("\n".join(urls) + "\n", encoding="utf-8")
print(f"rubrics={len(rows)} chunks={(len(rows)+size-1)//size} chunk_size={size}")
PY

run_chunk() {
  local urlfile="$1"
  local base
  base="$(basename "$urlfile" .urls)"
  local outfile="$RESULTS/${base}.json"
  local donefile="$RESULTS/${base}.done"
  local logfile="$RESULTS/${base}.log"

  if [[ -s "$outfile" && -f "$donefile" ]]; then
    echo "[skip] $base"
    incremental_import "$outfile" "$base"
    return 0
  fi

  mapfile -t urls < "$urlfile"
  local expected=${#urls[@]}
  if [[ $expected -eq 0 ]]; then
    return 0
  fi

  echo "[parse] $base started ($expected rubrics)"
  rm -f "$outfile" "$donefile" "$logfile"

  local rc=0
  if X1_2GIS_PARSER_MEMORY_LIMIT_MB="$CONTAINER_MEMORY" \
    docker compose -f docker-compose.2gis.yml --profile 2gis run --rm parser-2gis \
      -i "${urls[@]}" \
      -o "/data/moscow_bulk/results/${base}.json" \
      -f json \
      --chrome.binary_path /usr/bin/chromium \
      --chrome.headless yes \
      --chrome.disable-images yes \
      --chrome.silent-browser yes \
      --chrome.memory-limit "$CHROME_MEMORY" \
      --parser.use-gc yes \
      --parser.gc-pages-interval 20 \
      --parser.max-records "$MAX_RECORDS" \
      --parser.skip-404-response yes \
      --parser.delay_between_clicks "$DELAY_MS" \
      --writer.verbose no >"$logfile" 2>&1; then
    rc=0
  else
    rc=$?
  fi

  local completed=0
  local errors=0
  if [[ -f "$logfile" ]]; then
    completed=$(grep -c "Парсинг ссылки завершён" "$logfile" 2>/dev/null || true)
    errors=$(grep -c "Ошибка во время работы парсера" "$logfile" 2>/dev/null || true)
  fi

  if [[ $rc -eq 0 && -s "$outfile" && $completed -ge $expected && $errors -eq 0 ]]; then
    touch "$donefile"
    echo "[done] $base ($completed/$expected)"
  elif [[ -s "$outfile" ]]; then
    echo "[partial] $base rc=$rc completed=$completed/$expected errors=$errors; keeping recoverable JSON"
  else
    echo "[failed] $base rc=$rc completed=$completed/$expected errors=$errors; see $logfile"
  fi

  incremental_import "$outfile" "$base"
  return 0
}

echo "[3/5] Parsing Moscow with $WORKERS parallel Chrome workers..."
echo "      Live progress will print every ${PROGRESS_INTERVAL}s. You can also run: bash scripts/check_parser_2gis_moscow_progress.sh"
monitor_progress &
MONITOR_PID=$!
trap 'kill "$MONITOR_PID" 2>/dev/null || true' EXIT INT TERM

running=0
for urlfile in "$CHUNKS"/chunk_s"$(printf '%03d' "$CHUNK_SIZE")"_*.urls; do
  [[ -e "$urlfile" ]] || continue
  run_chunk "$urlfile" &
  running=$((running + 1))
  if (( running >= WORKERS )); then
    wait -n || true
    running=$((running - 1))
  fi
done
wait || true
kill "$MONITOR_PID" 2>/dev/null || true
progress_snapshot

echo "[4/5] Final idempotent import of every completed/recoverable JSON file..."
docker compose exec -T -e PYTHONPATH=/app app \
  python /app/scripts/import_2gis_directory.py \
  /app/data/2gis/moscow_bulk/results \
  --city "Москва"

echo "[5/5] Coverage report..."
docker compose exec -T -e PYTHONPATH=/app app \
  python /app/scripts/check_2gis_moscow_coverage.py

echo "Bulk run finished. Re-running the same command resumes completed chunks instead of downloading them again."
