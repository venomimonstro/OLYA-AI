#!/usr/bin/env bash
set -euo pipefail
umask 077

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

WITH_INFERENCE=1
DOCTOR_ONLY=0
SKIP_E2E=0
for arg in "$@"; do
  case "$arg" in
    --no-inference) WITH_INFERENCE=0 ;;
    --doctor-only) DOCTOR_ONLY=1 ;;
    --skip-e2e) SKIP_E2E=1 ;;
    -h|--help) echo "Usage: bash scripts/install.sh [--no-inference] [--doctor-only] [--skip-e2e]"; exit 0 ;;
    *) echo "Unknown option: $arg" >&2; exit 2 ;;
  esac
done

info() { printf '[X1] %s\n' "$*"; }
fail() { printf '[X1] ERROR: %s\n' "$*" >&2; exit 1; }

if [ "$(id -u)" -ne 0 ]; then
  command -v sudo >/dev/null 2>&1 || fail "Run installer as root or install sudo"
  exec sudo -E bash "$0" "$@"
fi
[[ "$(uname -s)" == "Linux" ]] || fail "Linux is required"
[[ "$(uname -m)" == "x86_64" ]] || fail "x86_64 is required by the supported CPU inference profile"

install_host_packages() {
  if ! command -v apt-get >/dev/null 2>&1; then
    command -v docker >/dev/null 2>&1 || fail "Automatic Docker installation currently supports Debian/Ubuntu"
    command -v python3 >/dev/null 2>&1 || fail "python3 is required"
    return
  fi
  local need=0
  for cmd in python3 git curl docker; do command -v "$cmd" >/dev/null 2>&1 || need=1; done
  if [ "$need" -eq 1 ]; then
    info "Installing host prerequisites"
    apt-get update
    DEBIAN_FRONTEND=noninteractive apt-get install -y ca-certificates curl git python3 docker.io
  fi
  if command -v systemctl >/dev/null 2>&1; then systemctl enable --now docker >/dev/null 2>&1 || true; fi
  if ! docker compose version >/dev/null 2>&1; then
    apt-get update
    DEBIAN_FRONTEND=noninteractive apt-get install -y docker-compose-v2 \
      || DEBIAN_FRONTEND=noninteractive apt-get install -y docker-compose-plugin \
      || fail "Docker Compose v2 plugin is required"
  fi
}

install_host_packages
command -v python3 >/dev/null 2>&1 || fail "python3 is required"
docker info >/dev/null 2>&1 || fail "Docker daemon is unavailable"
docker compose version >/dev/null 2>&1 || fail "Docker Compose v2 is unavailable"
if [ "$DOCTOR_ONLY" -eq 1 ]; then exec python3 scripts/doctor.py; fi

ram_kb=$(awk '/MemTotal/ {print $2}' /proc/meminfo)
ram_gb=$((ram_kb / 1024 / 1024))
disk_gb=$(df -Pk "$ROOT" | awk 'NR==2 {print int($4/1024/1024)}')
cores=$(nproc)
if [ "$WITH_INFERENCE" -eq 1 ]; then
  (( ram_gb >= 28 )) || fail "Qwen3-30B-A3B Q4_K_M requires at least 28 GB RAM in the supported X1 profile; found ${ram_gb} GB"
  (( disk_gb >= 60 )) || fail "At least 60 GB free disk is required for model + containers + backups; found ${disk_gb} GB"
else
  (( ram_gb >= 6 )) || fail "At least 6 GB RAM is required for the control plane"
  (( disk_gb >= 15 )) || fail "At least 15 GB free disk is required"
fi
if (( ram_gb < 32 )); then safe_context=8192
elif (( ram_gb < 48 )); then safe_context=12288
else safe_context=16384
fi
threads=$cores; (( threads > 2 )) && threads=$((threads - 1)); (( threads > 24 )) && threads=24; (( threads < 2 )) && threads=2
grep -qE 'avx2|avx512' /proc/cpuinfo || info "WARNING: AVX2/AVX512 not detected; local inference can be very slow"

mkdir -p data models backups
bash scripts/migrate_legacy_data.sh
chown -R 10001:10001 data backups
chmod 750 data backups models

new_env=0
if [ ! -f .env ]; then [ -f .env.example ] || fail ".env.example is missing"; cp .env.example .env; new_env=1; fi
db_password=$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')
admin_token=$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')
runtime_secret=$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')
sandbox_token=$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')
host_data_root="$(realpath "$ROOT/data")"

python3 - "$new_env" "$db_password" "$admin_token" "$runtime_secret" "$sandbox_token" "$host_data_root" "$safe_context" "$threads" <<'PY'
from pathlib import Path
import sys
path=Path('.env'); text=path.read_text('utf-8')
new_env=bool(int(sys.argv[1])); generated_db,generated_admin,generated_runtime,generated_sandbox,host_data=sys.argv[2:7]
safe_context=int(sys.argv[7]); threads=int(sys.argv[8]); lines=text.splitlines(); values={}
for line in lines:
    if line and not line.lstrip().startswith('#') and '=' in line:
        k,v=line.split('=',1); values[k]=v

def setv(key,value):
    global lines
    prefix=key+'='; found=False; out=[]
    for line in lines:
        if line.startswith(prefix):
            if not found: out.append(prefix+str(value)); found=True
        else: out.append(line)
    if not found: out.append(prefix+str(value))
    lines=out; values[key]=str(value)

def ensure_secret(key, generated, bad):
    current=values.get(key,'')
    if not current or current in bad: setv(key,generated)

setv('X1_ENV','production'); setv('X1_BIND_ADDRESS','127.0.0.1'); setv('X1_HOST_DATA_ROOT',host_data)
ensure_secret('POSTGRES_PASSWORD',generated_db,{'change-me-db'}); db=values['POSTGRES_PASSWORD']; setv('X1_DATABASE_URL',f'postgresql+psycopg://x1:{db}@db:5432/x1')
ensure_secret('X1_ADMIN_BOOTSTRAP_TOKEN',generated_admin,{'change-me'}); ensure_secret('X1_PROJECT_RUNTIME_SECRET_KEY',generated_runtime,{'change-me-runtime-secret'}); ensure_secret('X1_PROJECT_SANDBOX_WORKER_TOKEN',generated_sandbox,{'change-me-sandbox-worker'})
setv('X1_LLAMA_MODEL_NAME','Qwen3-30B-A3B-Q4_K_M'); setv('X1_LLAMA_BASE_URL','http://llama:8080'); setv('X1_PROJECT_SANDBOX_BACKEND','remote'); setv('X1_PROJECT_SANDBOX_IMAGE','x1-sandbox:0.39'); setv('X1_PROJECT_SANDBOX_WORKER_URL','http://sandbox-worker:8090')
if values.get('X1_SEARCH_PROVIDER','') in {'','disabled'}: setv('X1_SEARCH_PROVIDER','searxng')
if values.get('X1_SEARCH_PROVIDERS','') in {'','disabled'}: setv('X1_SEARCH_PROVIDERS','searxng')
setv('X1_SEARXNG_BASE_URL','http://searxng:8080')
# Production AI access is always protected by the measured rollout gate. A full
# launch is represented by a 100% rollout, not by disabling the guard itself.
setv('X1_PUBLIC_LAUNCH_ENFORCE_EXPOSURE','true')
if new_env:
    setv('X1_MAX_CONTEXT_TOKENS',min(8192,safe_context)); setv('X1_DEEP_CONTEXT_TOKENS',safe_context); setv('X1_LLAMA_THREADS',threads); setv('X1_LLAMA_THREADS_BATCH',threads)
else:
    try: current=int(values.get('X1_DEEP_CONTEXT_TOKENS',safe_context))
    except ValueError: current=safe_context
    if current>safe_context: setv('X1_DEEP_CONTEXT_TOKENS',safe_context)
    if not values.get('X1_LLAMA_THREADS'): setv('X1_LLAMA_THREADS',threads)
    if not values.get('X1_LLAMA_THREADS_BATCH'): setv('X1_LLAMA_THREADS_BATCH',threads)
for key,value in {'X1_HTTP_LIMIT_CONCURRENCY':'128','X1_HTTP_BACKLOG':'2048','X1_HTTP_KEEPALIVE_SECONDS':'5'}.items():
    if not values.get(key): setv(key,value)
path.write_text('\n'.join(lines).rstrip()+'\n','utf-8')
PY
chmod 600 .env
info "Production configuration prepared (RAM=${ram_gb}GB, CPU=${cores}, safe initial context=${safe_context})"

if [ "$WITH_INFERENCE" -eq 1 ]; then info "Downloading/verifying official Qwen3-30B-A3B Q4_K_M GGUF (resumable)"; python3 scripts/download_model.py; fi

info "Pulling pinned runtime images"
docker compose pull db searxng >/dev/null
if [ "$WITH_INFERENCE" -eq 1 ]; then docker compose --profile inference pull llama >/dev/null; fi
info "Building hardened closed-development sandbox runtime"
docker build --pull -f Dockerfile.sandbox-runtime -t x1-sandbox:0.39 .
info "Validating Compose configuration"; docker compose config --quiet
info "Building X1 services"; docker compose build app sandbox-worker

info "Starting database, private search and sandbox worker"
docker compose up -d db searxng sandbox-worker
for _ in $(seq 1 90); do
  db_ok=0; search_ok=0; sandbox_ok=0
  docker compose exec -T db pg_isready -U x1 -d x1 >/dev/null 2>&1 && db_ok=1
  docker compose exec -T searxng python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/search?q=x1&format=json',timeout=5).read()" >/dev/null 2>&1 && search_ok=1
  docker compose exec -T sandbox-worker python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8090/health',timeout=3).read()" >/dev/null 2>&1 && sandbox_ok=1
  [ "$db_ok$search_ok$sandbox_ok" = "111" ] && break
  sleep 2
done
docker compose exec -T db pg_isready -U x1 -d x1 >/dev/null 2>&1 || fail "PostgreSQL did not become ready"
docker compose exec -T searxng python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/search?q=x1&format=json',timeout=5).read()" >/dev/null 2>&1 || fail "SearXNG did not become ready"
docker compose exec -T sandbox-worker python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8090/health',timeout=3).read()" >/dev/null 2>&1 || fail "Sandbox worker did not become ready"

info "Applying database migrations"; docker compose run --rm --no-deps app alembic upgrade head
if [ "$WITH_INFERENCE" -eq 1 ]; then info "Starting Qwen llama.cpp and X1"; docker compose --profile inference up -d llama app
else info "Starting X1 control plane without inference"; docker compose up -d app; fi

for _ in $(seq 1 180); do
  if python3 - <<'PY' >/dev/null 2>&1
from urllib.request import urlopen
urlopen('http://127.0.0.1:8000/health',timeout=3).read()
PY
  then break; fi
  sleep 2
done
python3 - <<'PY' >/dev/null 2>&1 || fail "X1 API did not become healthy"
from urllib.request import urlopen
urlopen('http://127.0.0.1:8000/health',timeout=5).read()
PY

if [ "$WITH_INFERENCE" -eq 1 ]; then
  info "Waiting for Qwen model readiness"
  for _ in $(seq 1 180); do
    docker compose exec -T app python - <<'PY' >/dev/null 2>&1 && break
from urllib.request import urlopen
urlopen('http://llama:8080/health',timeout=4).read()
PY
    sleep 3
  done
  docker compose exec -T app python - <<'PY' >/dev/null 2>&1 || fail "llama.cpp/Qwen did not become ready"
from urllib.request import urlopen
urlopen('http://llama:8080/health',timeout=5).read()
PY
fi

info "Running hardened sandbox execution probe"; docker compose exec -T app python -m scripts.sandbox_probe
if [ "$WITH_INFERENCE" -eq 1 ]; then
  info "Running production release gate, user journey and chaos simulations"
  gate_args=(--runtime --live-inference --user-journey --chaos); [ "$SKIP_E2E" -eq 1 ] && gate_args=(--runtime --live-inference)
  set +e; python3 scripts/release_gate.py "${gate_args[@]}"; gate_status=$?; set -e
  if [ "$gate_status" -ne 0 ]; then docker compose ps >&2 || true; docker compose logs --tail=120 app llama searxng sandbox-worker >&2 || true; fail "Production release gate failed; inspect backups/release-gate-latest.json"; fi
else
  info "Creating and restore-testing initial backup"; backup_path=$(bash scripts/backup.sh); bash scripts/restore_drill.sh "$backup_path" >/dev/null
fi

info "Running X1 doctor"
set +e; python3 scripts/doctor.py; doctor_status=$?; set -e
if [ "$WITH_INFERENCE" -eq 1 ] && [ "$doctor_status" -ne 0 ]; then fail "Doctor reported a non-stable production system"; fi
if [ "$WITH_INFERENCE" -eq 0 ] && [ "$doctor_status" -eq 2 ]; then fail "Control plane has critical failures"; fi
info "Installation complete"
info "X1: http://127.0.0.1:${X1_PORT:-8000} (bind is local-only; use a TLS reverse proxy for public access)"
info "Admin bootstrap token is stored only in $ROOT/.env (mode 600)."
info "Public registration/login are available; expensive AI access remains controlled by rollout policy until a user is eligible."
