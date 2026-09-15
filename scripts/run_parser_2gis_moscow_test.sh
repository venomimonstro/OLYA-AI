#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
mkdir -p data/2gis

OUT="/data/moscow_autoservices.json"
URL="https://2gis.ru/moscow/search/%D0%90%D0%B2%D1%82%D0%BE%D1%81%D0%B5%D1%80%D0%B2%D0%B8%D1%81%D1%8B"

echo "[1/4] Building isolated parser-2gis image..."
docker compose -f docker-compose.2gis.yml --profile 2gis build parser-2gis

echo "[2/4] Parsing up to 50 Moscow autoservices into data/2gis/moscow_autoservices.json..."
rm -f data/2gis/moscow_autoservices.json
docker compose -f docker-compose.2gis.yml --profile 2gis run --rm parser-2gis \
  -i "$URL" \
  -o "$OUT" \
  -f json \
  --chrome.binary_path /usr/bin/chromium \
  --chrome.headless yes \
  --chrome.disable-images yes \
  --chrome.silent-browser yes \
  --chrome.memory-limit 1200 \
  --parser.use-gc yes \
  --parser.gc-pages-interval 5 \
  --parser.max-records 50 \
  --parser.delay_between_clicks 50 \
  --writer.verbose yes

if [[ ! -s data/2gis/moscow_autoservices.json ]]; then
  echo "ERROR: parser produced no JSON output" >&2
  exit 2
fi

echo "[3/4] Importing raw 2GIS cards into OLYA owned index..."
docker compose exec -T -e PYTHONPATH=/app app \
  python /app/scripts/import_2gis_json.py \
  /app/data/2gis/moscow_autoservices.json \
  --city "Москва" \
  --category car_repair

echo "[4/4] Verifying offline local search..."
docker compose exec -T -e PYTHONPATH=/app app \
  python /app/scripts/check_local_business_route.py \
  --offline \
  "лучшие автосервисы в москве"
