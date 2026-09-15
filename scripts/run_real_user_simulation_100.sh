#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# By default the benchmark creates isolated temporary Free users inside the app,
# distributes scenarios across them so daily quota does not distort results, and
# deletes them afterwards. Explicit credentials remain available for targeted
# single-account debugging only.
if [ -n "${OLYA_BENCH_TOKEN:-}" ]; then
  docker compose exec -T \
    -e OLYA_BENCH_TOKEN="$OLYA_BENCH_TOKEN" \
    app python -m scripts.real_user_live_simulation_100 "$@"
  exit $?
fi

if [ -n "${OLYA_BENCH_EMAIL:-}" ] && [ -n "${OLYA_BENCH_PASSWORD:-}" ]; then
  docker compose exec -T \
    -e OLYA_BENCH_EMAIL="$OLYA_BENCH_EMAIL" \
    -e OLYA_BENCH_PASSWORD="$OLYA_BENCH_PASSWORD" \
    app python -m scripts.real_user_live_simulation_100 "$@"
  exit $?
fi

docker compose exec -T app python -m scripts.real_user_simulation_orchestrator "$@"
