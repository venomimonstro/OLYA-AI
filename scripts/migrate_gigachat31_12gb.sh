#!/usr/bin/env bash
set -euo pipefail
umask 077

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

info(){ printf '[OLYA] %s\n' "$*"; }
fail(){ printf '[OLYA] ERROR: %s\n' "$*" >&2; exit 1; }

[ -f .env ] || fail ".env not found"
[ -f model-manifest.json ] || fail "model-manifest.json not found"
command -v docker >/dev/null 2>&1 || fail "docker is required"
docker compose version >/dev/null 2>&1 || fail "Docker Compose v2 is required"
command -v python3 >/dev/null 2>&1 || fail "python3 is required"

ram_kb=$(awk '/MemTotal/ {print $2}' /proc/meminfo)
ram_gib=$(python3 - "$ram_kb" <<'PY'
import sys
print(round(int(sys.argv[1])/1024/1024,3))
PY
)
python3 - "$ram_kb" <<'PY'
import sys
ram=int(sys.argv[1])/1024/1024
if ram < 11.0:
    raise SystemExit(f"GigaChat 3.1 Q4 production profile requires >=11 GiB detected RAM; found {ram:.2f} GiB")
PY

free_gb=$(df -Pk "$ROOT" | awk 'NR==2 {print int($4/1024/1024)}')
(( free_gb >= 12 )) || fail "At least 12 GB free disk is required for the 6.47 GB model plus safe partial download; found ${free_gb} GB"

# Fail before changing the running stack if the migration/audit files themselves
# are malformed or Compose cannot resolve the current installation.
bash -n scripts/migrate_gigachat31_12gb.sh
python3 -m py_compile scripts/gigachat31_runtime_audit.py scripts/download_model.py
docker compose config --quiet

stamp=$(date -u +%Y%m%dT%H%M%SZ)
backup="backups/env-before-gigachat31-${stamp}"
mkdir -p backups models
cp -a .env "$backup"
chmod 600 "$backup"
info "Saved rollback environment: $backup"

rollback(){
  rc=$?
  trap - ERR INT TERM
  if [ "$rc" -ne 0 ]; then
    printf '[OLYA] Migration failed; restoring previous .env and containers\n' >&2
    cp -a "$backup" .env || true
    docker compose up -d --force-recreate llama >/dev/null 2>&1 || true
    docker compose up -d --build --force-recreate app >/dev/null 2>&1 || true
  fi
  exit "$rc"
}
trap rollback ERR INT TERM

info "Downloading and verifying pinned GigaChat 3.1 Lightning Q4_K_M"
python3 scripts/download_model.py --profile primary --retries 6 --timeout 180
python3 scripts/download_model.py --profile primary --verify-only

info "Applying 12 GiB runtime envelope"
python3 - <<'PY'
from pathlib import Path

path=Path('.env')
lines=path.read_text('utf-8').splitlines()
updates={
    'X1_SERVER_OPTIMIZATION_PROFILE':'gigachat31_12gb',
    'X1_LLAMA_MODEL_NAME':'GigaChat3.1-10B-A1.8B-Q4_K_M',
    'X1_LLAMA_MODEL_FILE':'GigaChat3.1-10B-A1.8B-q4_K_M.gguf',
    'X1_LLAMA_BASE_URL':'http://llama:8080',
    'X1_LLAMA_MEMORY_LIMIT':'8g',
    'X1_MAX_CONTEXT_TOKENS':'4096',
    'X1_DEEP_CONTEXT_TOKENS':'4096',
    'X1_MAX_CONCURRENT_GENERATIONS':'1',
    'X1_INFERENCE_MAX_QUEUED_PER_PRINCIPAL':'4',
    'X1_MAX_QUEUE_SIZE':'24',
    'X1_REQUEST_TIMEOUT_SECONDS':'180',
    'X1_DEFAULT_MAX_OUTPUT_TOKENS':'1024',
}
seen=set(); out=[]
for line in lines:
    if line and not line.lstrip().startswith('#') and '=' in line:
        key=line.split('=',1)[0]
        if key in updates:
            if key not in seen:
                out.append(f'{key}={updates[key]}'); seen.add(key)
            continue
    out.append(line)
for key,value in updates.items():
    if key not in seen:
        out.append(f'{key}={value}')
path.write_text('\n'.join(out).rstrip()+'\n','utf-8')
PY
chmod 600 .env

info "Validating resolved GigaChat Compose configuration"
resolved_compose=$(mktemp)
docker compose config > "$resolved_compose"
grep -Fq '/models/GigaChat3.1-10B-A1.8B-q4_K_M.gguf' "$resolved_compose" || { rm -f "$resolved_compose"; fail "Compose did not resolve the GigaChat model file"; }
rm -f "$resolved_compose"

info "Rebuilding application runtime and restarting local inference"
docker compose up -d --build --force-recreate llama app

info "Waiting for GigaChat llama.cpp health"
ready=0
for _ in $(seq 1 90); do
  if docker compose exec -T llama sh -lc "curl -fsS http://127.0.0.1:8080/health >/dev/null" >/dev/null 2>&1; then
    ready=1; break
  fi
  sleep 2
done
[ "$ready" -eq 1 ] || { docker compose logs --tail=160 llama >&2 || true; fail "GigaChat llama.cpp did not become healthy"; }

info "Verifying model artifact inside host storage"
python3 scripts/download_model.py --profile primary --verify-only

info "Running live GigaChat 3.1 integration audit"
docker compose exec -T app python -m scripts.gigachat31_runtime_audit

info "Running core answer-pipeline sanity checks"
docker compose exec -T app python -m scripts.answer_pipeline_audit

trap - ERR INT TERM
info "GigaChat 3.1 migration completed successfully (RAM=${ram_gib}GiB, context=4096, llama_limit=8g)"
info "Rollback env retained at: $backup"
