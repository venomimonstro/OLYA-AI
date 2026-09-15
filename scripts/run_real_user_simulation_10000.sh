#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

printf '[OLYA-10K] Static routing/intent simulation: all 10,000 sessions\n'
docker compose exec -T app python -m scripts.real_user_routing_audit_10000

printf '[OLYA-10K] Live site + AI simulation\n'
if [ "${1:-}" = "--full" ]; then
  shift
  echo '[OLYA-10K] WARNING: full 10,000 live generations on one CPU inference slot can run for many hours/days.'
  docker compose exec -T app python -m scripts.real_user_live_simulation_10000 --full "$@"
else
  docker compose exec -T app python -m scripts.real_user_live_simulation_10000 "$@"
fi
