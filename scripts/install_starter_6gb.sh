#!/usr/bin/env bash
set -euo pipefail
umask 077

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

info() { printf '[X1 starter] %s\n' "$*"; }
fail() { printf '[X1 starter] ERROR: %s\n' "$*" >&2; exit 1; }

if [ "$(id -u)" -ne 0 ]; then
  command -v sudo >/dev/null 2>&1 || fail "Run as root or install sudo"
  exec sudo -E bash "$0" "$@"
fi
[[ "$(uname -s)" == "Linux" ]] || fail "Linux is required"
[[ "$(uname -m)" == "x86_64" ]] || fail "x86_64 is required"

for cmd in python3 git curl docker; do command -v "$cmd" >/dev/null 2>&1 || fail "$cmd is required"; done
docker info >/dev/null 2>&1 || fail "Docker daemon is unavailable"
docker compose version >/dev/null 2>&1 || fail "Docker Compose v2 is required"

ram_kb=$(awk '/MemTotal/ {print $2}' /proc/meminfo)
ram_gib=$((ram_kb / 1024 / 1024))
cores=$(nproc)
disk_gb=$(df -Pk "$ROOT" | awk 'NR==2 {print int($4/1024/1024)}')
(( ram_kb >= 5*1024*1024 )) || fail "Starter profile needs at least 5 GiB RAM; found about ${ram_gib} GiB"
(( disk_gb >= 25 )) || fail "Starter profile needs at least 25 GB free disk; found ${disk_gb} GB"
if (( cores < 4 )); then info "WARNING: ${cores} CPU cores detected; 4 cores are recommended"; fi

mkdir -p data models backups
chown -R 10001:10001 data backups
chmod 750 data backups models

if ! swapon --show=NAME --noheadings 2>/dev/null | grep -q .; then
  info "No swap detected; attempting a 2 GiB emergency swapfile"
  if fallocate -l 2G /swapfile 2>/dev/null || dd if=/dev/zero of=/swapfile bs=1M count=2048 status=none; then
    chmod 600 /swapfile
    if mkswap /swapfile >/dev/null 2>&1 && swapon /swapfile >/dev/null 2>&1; then
      grep -q '^/swapfile ' /etc/fstab 2>/dev/null || printf '/swapfile none swap sw 0 0\n' >> /etc/fstab
      sysctl -w vm.swappiness=10 >/dev/null 2>&1 || true
      info "2 GiB emergency swap enabled (not used as normal inference RAM)"
    else
      rm -f /swapfile
      info "WARNING: host does not permit swap; continuing with stricter memory limits"
    fi
  fi
fi

new_env=0
if [ ! -f .env ]; then
  [ -f .env.example ] || fail ".env.example is missing"
  cp .env.example .env
  new_env=1
fi

postgres_password=$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')
admin_token=$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')
runtime_secret=$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')
sandbox_token=$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')
document_token=$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')
payment_secret=$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')
host_data_root="$(realpath "$ROOT/data")"
threads=$cores; (( threads > 3 )) && threads=3; (( threads < 2 )) && threads=2

python3 - "$new_env" "$postgres_password" "$admin_token" "$runtime_secret" "$sandbox_token" "$document_token" "$payment_secret" "$host_data_root" "$threads" <<'PY'
from pathlib import Path
import sys

path=Path('.env')
lines=path.read_text('utf-8').splitlines()
new=bool(int(sys.argv[1]))
generated_db, generated_admin, generated_runtime, generated_sandbox, generated_document, generated_payment, host_data = sys.argv[2:9]
threads=int(sys.argv[9])
values={}
for line in lines:
    if line and not line.lstrip().startswith('#') and '=' in line:
        key,value=line.split('=',1); values[key]=value

def setv(key,value):
    global lines
    prefix=key+'='; found=False; out=[]
    for line in lines:
        if line.startswith(prefix):
            if not found: out.append(prefix+str(value)); found=True
        else: out.append(line)
    if not found: out.append(prefix+str(value))
    lines=out; values[key]=str(value)

def secret(key,generated,bad):
    current=values.get(key,'')
    if new or not current or current in bad: setv(key,generated)

setv('X1_ENV','production')
setv('X1_SERVER_OPTIMIZATION_PROFILE','starter_6gb')
setv('X1_BIND_ADDRESS','127.0.0.1')
setv('X1_HOST_DATA_ROOT',host_data)
secret('POSTGRES_PASSWORD',generated_db,{'change-me-db'})
setv('X1_DATABASE_URL',f"postgresql+psycopg://x1:{values['POSTGRES_PASSWORD']}@db:5432/x1")
secret('X1_ADMIN_BOOTSTRAP_TOKEN',generated_admin,{'change-me'})
secret('X1_PROJECT_RUNTIME_SECRET_KEY',generated_runtime,{'change-me-runtime-secret'})
secret('X1_PROJECT_SANDBOX_WORKER_TOKEN',generated_sandbox,{'change-me-sandbox-worker'})
secret('X1_DOCUMENT_RENDER_WORKER_TOKEN',generated_document,{'change-me-document-worker'})
secret('X1_PAYMENT_INGEST_SECRET',generated_payment,{''})

setv('X1_LLAMA_MODEL_NAME','Qwen3-4B-Q4_K_M')
setv('X1_LLAMA_MODEL_FILE','Qwen3-4B-Q4_K_M.gguf')
setv('X1_LLAMA_BASE_URL','http://llama:8080')
setv('X1_MAX_CONTEXT_TOKENS','4096')
setv('X1_DEEP_CONTEXT_TOKENS','4096')
setv('X1_LLAMA_MEMORY_LIMIT','3200m')
setv('X1_LLAMA_THREADS',threads)
setv('X1_LLAMA_THREADS_BATCH',threads)
setv('X1_MAX_CONCURRENT_GENERATIONS','1')
setv('X1_MAX_QUEUE_SIZE','16')
setv('X1_INFERENCE_MAX_QUEUED_PER_PRINCIPAL','1')
setv('X1_INFERENCE_QUEUE_TIMEOUT_SECONDS','90')
setv('X1_DEFAULT_MAX_OUTPUT_TOKENS','768')
setv('X1_REQUEST_TIMEOUT_SECONDS','150')

setv('X1_DB_MEMORY_LIMIT_MB','384')
setv('X1_SEARX_MEMORY_LIMIT_MB','256')
setv('X1_APP_MEMORY_LIMIT_MB','768')
setv('X1_DATABASE_POOL_SIZE','3')
setv('X1_DATABASE_MAX_OVERFLOW','1')
setv('X1_HTTP_LIMIT_CONCURRENCY','32')
setv('X1_HTTP_BACKLOG','512')
setv('X1_HTTP_KEEPALIVE_SECONDS','5')

# Keep the profitable starter core online. Heavy executors can be enabled after
# a RAM upgrade without changing user/project data.
setv('X1_PROJECT_SANDBOX_BACKEND','disabled')
setv('X1_DOCUMENT_RENDER_BACKEND','disabled')
setv('X1_IMAGE_BACKEND','disabled')
setv('X1_IMAGE_EDIT_BACKEND','disabled')
setv('X1_IMAGE_VISION_QA_URL','')
setv('X1_PUBLIC_LAUNCH_ENFORCE_EXPOSURE','true')

# Request-unit plan defaults. Actual CPU seconds remain a second hard ceiling.
for key,value in {
    'X1_PLAN_MONTHLY_REQUEST_UNITS_FREE':'30','X1_PLAN_MONTHLY_REQUEST_UNITS_X1':'240',
    'X1_PLAN_MONTHLY_REQUEST_UNITS_PRO':'720','X1_PLAN_MONTHLY_REQUEST_UNITS_MAX':'1800',
    'X1_PLAN_MONTHLY_REQUEST_UNITS_BUSINESS':'4800','X1_PLAN_DAILY_REQUEST_UNITS_FREE':'6',
    'X1_PLAN_DAILY_REQUEST_UNITS_X1':'24','X1_PLAN_DAILY_REQUEST_UNITS_PRO':'60',
    'X1_PLAN_DAILY_REQUEST_UNITS_MAX':'120','X1_PLAN_DAILY_REQUEST_UNITS_BUSINESS':'300',
    'X1_REQUEST_UNIT_WEIGHT_FAST':'1','X1_REQUEST_UNIT_WEIGHT_WORK':'2',
    'X1_REQUEST_UNIT_WEIGHT_DEEP':'4','X1_REQUEST_UNIT_WEIGHT_API':'2',
}.items(): setv(key,value)

path.write_text('\n'.join(lines).rstrip()+'\n','utf-8')
PY
chmod 600 .env

info "Downloading/verifying pinned Qwen3-4B Q4_K_M (~2.5 GB)"
python3 scripts/download_model.py --profile primary

info "Pulling compact runtime images"
docker compose pull db searxng >/dev/null
docker compose --profile inference pull llama >/dev/null
info "Building starter app without LibreOffice/Poppler"
docker compose build --build-arg X1_RUNTIME_PROFILE=starter_6gb app
info "Validating Compose configuration"
docker compose config --quiet

info "Starting PostgreSQL and SearXNG"
docker compose up -d db searxng
for _ in $(seq 1 60); do
  docker compose exec -T db pg_isready -U x1 -d x1 >/dev/null 2>&1 && break
  sleep 2
done
docker compose exec -T db pg_isready -U x1 -d x1 >/dev/null 2>&1 || fail "PostgreSQL did not become ready"

info "Applying database migrations"
docker compose run --rm --no-deps app alembic upgrade head
info "Starting one-slot Qwen inference and X1"
docker compose --profile inference up -d llama app

for _ in $(seq 1 150); do
  if curl -fsS http://127.0.0.1:${X1_PORT:-8000}/health >/dev/null 2>&1; then break; fi
  sleep 2
done
curl -fsS http://127.0.0.1:${X1_PORT:-8000}/health >/dev/null || fail "X1 API did not become healthy"
for _ in $(seq 1 150); do
  if docker compose exec -T app python -c "import urllib.request; urllib.request.urlopen('http://llama:8080/health',timeout=4).read()" >/dev/null 2>&1; then break; fi
  sleep 2
done
docker compose exec -T app python -c "import urllib.request; urllib.request.urlopen('http://llama:8080/health',timeout=5).read()" >/dev/null 2>&1 || fail "Qwen3-4B did not become ready"
python3 scripts/download_model.py --profile primary --verify-only >/dev/null || fail "Pinned Qwen3-4B integrity check failed"

info "Running starter profile contract audit"
python3 -m scripts.starter_6gb_audit
info "Creating and restore-testing initial backup"
backup_path=$(bash scripts/backup.sh)
bash scripts/restore_drill.sh "$backup_path" >/dev/null

info "Starter installation complete: ${cores} CPU / about ${ram_gib} GiB RAM / ${disk_gb} GB free before installation"
info "Always-on services: PostgreSQL + SearXNG + app + Qwen3-4B. One inference runs at a time; bursts wait in the bounded fair queue."
info "Sandbox, document rendering and local image generation are intentionally disabled on starter_6gb and can be enabled after a RAM upgrade."
info "Configure X1_BILLING_CHECKOUT_URL_TEMPLATE before accepting paid purchases. Payment ingest secret was generated in .env."
info "X1 binds to localhost; put a TLS reverse proxy in front of it for public access."
