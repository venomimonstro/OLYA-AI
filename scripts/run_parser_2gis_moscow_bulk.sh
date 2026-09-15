#!/usr/bin/env bash
set -uo pipefail

cd "$(dirname "$0")/.."

WORKERS="${PARSER_2GIS_WORKERS:-2}"
CHUNK_SIZE="${PARSER_2GIS_CHUNK_SIZE:-18}"
MAX_RECORDS="${PARSER_2GIS_MAX_RECORDS:-50000}"
CHROME_MEMORY="${PARSER_2GIS_CHROME_MEMORY_MB:-650}"
CONTAINER_MEMORY="${PARSER_2GIS_CONTAINER_MEMORY_MB:-900}"
DELAY_MS="${PARSER_2GIS_DELAY_MS:-0}"

ROOT="data/2gis/moscow_bulk"
CHUNKS="$ROOT/chunks"
RESULTS="$ROOT/results"
RUBRICS="$ROOT/rubrics.tsv"
mkdir -p "$CHUNKS" "$RESULTS"

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
    return 0
  fi

  mapfile -t urls < "$urlfile"
  if [[ ${#urls[@]} -eq 0 ]]; then
    return 0
  fi

  echo "[parse] $base (${#urls[@]} rubrics)"
  rm -f "$outfile" "$donefile"

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

  if [[ $rc -eq 0 && -s "$outfile" ]]; then
    touch "$donefile"
    echo "[done] $base"
  elif [[ -s "$outfile" ]]; then
    echo "[partial] $base rc=$rc; partial JSON kept and will still be imported"
  else
    echo "[failed] $base rc=$rc; see $logfile"
  fi
  return 0
}

echo "[3/5] Parsing Moscow with $WORKERS parallel Chrome workers..."
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

echo "[4/5] Importing all completed and recoverable partial JSON files..."
docker compose exec -T -e PYTHONPATH=/app app \
  python /app/scripts/import_2gis_directory.py \
  /app/data/2gis/moscow_bulk/results \
  --city "Москва"

echo "[5/5] Coverage report..."
docker compose exec -T -e PYTHONPATH=/app app \
  python /app/scripts/check_2gis_moscow_coverage.py

echo "Bulk run finished. Re-running the same command resumes completed chunks instead of downloading them again."
