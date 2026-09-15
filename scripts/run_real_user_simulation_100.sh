#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [ -n "${OLYA_BENCH_TOKEN:-}" ]; then
  docker compose exec -T \
    -e OLYA_BENCH_TOKEN="$OLYA_BENCH_TOKEN" \
    app python -m scripts.real_user_live_simulation_100 "$@"
  exit $?
fi

email="${OLYA_BENCH_EMAIL:-}"
password="${OLYA_BENCH_PASSWORD:-}"
if [ -z "$email" ]; then
  printf 'Email тестового пользователя OLYA: '
  IFS= read -r email
fi
if [ -z "$password" ]; then
  printf 'Пароль: '
  IFS= read -r -s password
  printf '\n'
fi

if [ -z "$email" ] || [ -z "$password" ]; then
  echo '[OLYA-BENCH] Email/password are required unless OLYA_BENCH_TOKEN is set.' >&2
  exit 2
fi

docker compose exec -T \
  -e OLYA_BENCH_EMAIL="$email" \
  -e OLYA_BENCH_PASSWORD="$password" \
  app python -m scripts.real_user_live_simulation_100 "$@"
