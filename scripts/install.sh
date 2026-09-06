#!/usr/bin/env bash
set -euo pipefail
umask 077

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

WITH_INFERENCE=1
DOCTOR_ONLY=0
for arg in "$@"; do
  case "$arg" in
    --no-inference) WITH_INFERENCE=0 ;;
    --doctor-only) DOCTOR_ONLY=1 ;;
    -h|--help)
      echo "Usage: bash scripts/install.sh [--no-inference] [--doctor-only]"
      exit 0
      ;;
    *) echo "Unknown option: $arg" >&2; exit 2 ;;
  esac
done

info() { printf '[X1] %s\n' "$*"; }
fail() { printf '[X1] ERROR: %s\n' "$*" >&2; exit 1; }

[[ "$(uname -s)" == "Linux" ]] || fail "Linux is required"
[[ "$(uname -m)" == "x86_64" ]] || fail "x86_64 is required by the current supported inference profile"
command -v python3 >/dev/null 2>&1 || fail "python3 is required"

install_docker_if_needed() {
  command -v docker >/dev/null 2>&1 && return 0
  [ -r /etc/os-release ] || fail "Docker is missing and Linux distribution cannot be detected"
  # shellcheck disable=SC1091
  . /etc/os-release
  case "${ID:-}" in
    debian|ubuntu)
      local elevate=""
      if [ "$(id -u)" -ne 0 ]; then
        command -v sudo >/dev/null 2>&1 || fail "sudo is required to install Docker packages"
        elevate="sudo"
      fi
      info "Installing Docker from distribution packages"
      $elevate apt-get update
      DEBIAN_FRONTEND=noninteractive $elevate apt-get install -y docker.io docker-compose-v2
      ;;
    *)
      fail "Docker is not installed. Install Docker Engine + Compose plugin for ${ID:-this distribution}, then rerun the installer"
      ;;
  esac
}

install_docker_if_needed
docker compose version >/dev/null 2>&1 || fail "Docker Compose v2 plugin is required"

if [ "$DOCTOR_ONLY" -eq 1 ]; then
  exec python3 scripts/doctor.py
fi

ram_kb=$(awk '/MemTotal/ {print $2}' /proc/meminfo)
ram_gb=$((ram_kb / 1024 / 1024))
disk_gb=$(df -Pk "$ROOT" | awk 'NR==2 {print int($4/1024/1024)}')
if [ "$WITH_INFERENCE" -eq 1 ]; then
  (( ram_gb >= 28 )) || fail "At least 28 GB RAM is required for the current local model profile; found ${ram_gb} GB"
  (( disk_gb >= 80 )) || fail "At least 80 GB free disk is required; found ${disk_gb} GB"
else
  (( ram_gb >= 4 )) || fail "At least 4 GB RAM is required for control-plane-only installation"
  (( disk_gb >= 10 )) || fail "At least 10 GB free disk is required"
fi

grep -qE 'avx2|avx512' /proc/cpuinfo || info "WARNING: AVX2/AVX512 not detected; local inference may be too slow"

if [ ! -f .env ]; then
  [ -f .env.example ] || fail ".env.example is missing"
  cp .env.example .env
  db_password=$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')
  admin_token=$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')
  runtime_secret=$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')
  python3 - "$db_password" "$admin_token" "$runtime_secret" <<'PY'
from pathlib import Path
import sys
path = Path('.env')
text = path.read_text('utf-8')
db, admin, runtime = sys.argv[1:4]
text = text.replace('X1_ENV=development', 'X1_ENV=production')
text = text.replace('POSTGRES_PASSWORD=change-me-db', f'POSTGRES_PASSWORD={db}')
text = text.replace('x1:change-me-db@db:5432/x1', f'x1:{db}@db:5432/x1')
text = text.replace('X1_ADMIN_BOOTSTRAP_TOKEN=change-me', f'X1_ADMIN_BOOTSTRAP_TOKEN={admin}')
text = text.replace('X1_PROJECT_RUNTIME_SECRET_KEY=change-me-runtime-secret', f'X1_PROJECT_RUNTIME_SECRET_KEY={runtime}')
path.write_text(text, 'utf-8')
PY
  chmod 600 .env
  info "Created production .env with generated secrets"
else
  info "Existing .env preserved; installer never rotates existing secrets automatically"
fi

mkdir -p models backups
if [ "$WITH_INFERENCE" -eq 1 ]; then
  MODEL="models/Qwen3.6-35B-A3B-Q4_K_M.gguf"
  [ -s "$MODEL" ] || fail "Local model is missing: $MODEL"
fi

info "Validating Compose configuration"
docker compose config --quiet

info "Building X1 application image"
docker compose build app

info "Starting PostgreSQL"
docker compose up -d db
for _ in $(seq 1 60); do
  if docker compose exec -T db pg_isready -U x1 -d x1 >/dev/null 2>&1; then
    break
  fi
  sleep 1
done
docker compose exec -T db pg_isready -U x1 -d x1 >/dev/null 2>&1 || fail "PostgreSQL did not become ready"

info "Applying database migrations"
docker compose run --rm app alembic upgrade head

if [ "$WITH_INFERENCE" -eq 1 ]; then
  info "Starting local inference and application"
  docker compose --profile inference up -d llama app
else
  info "Starting application without inference profile"
  docker compose up -d app
fi

for _ in $(seq 1 60); do
  if python3 - <<'PY' >/dev/null 2>&1
from urllib.request import urlopen
urlopen('http://127.0.0.1:8000/health', timeout=2).read()
PY
  then
    break
  fi
  sleep 1
done

# Establish a known-good recovery point immediately. Deep health checks can then
# verify this snapshot instead of a fresh production installation starting with
# an avoidable "no backup" warning.
info "Creating initial verified backup"
bash scripts/backup.sh >/dev/null

info "Running X1 doctor"
set +e
python3 scripts/doctor.py
DOCTOR_STATUS=$?
set -e
if [ "$WITH_INFERENCE" -eq 1 ] && [ "$DOCTOR_STATUS" -ne 0 ]; then
  fail "Installation completed but doctor reported a non-stable system"
fi
if [ "$WITH_INFERENCE" -eq 0 ] && [ "$DOCTOR_STATUS" -eq 2 ]; then
  fail "Control plane installation has critical failures"
fi

info "Installation complete"
info "Application is bound to 127.0.0.1 by default. Put a TLS reverse proxy in front of it for public access."
