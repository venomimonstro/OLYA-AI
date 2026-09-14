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

cores=$(nproc)
threads=$cores
(( threads > 12 )) && threads=12
(( threads < 2 )) && threads=2
batch_threads=$threads
info "Detected CPU threads=${cores}; GigaChat decode_threads=${threads}; batch_threads=${batch_threads}"

free_gb=$(df -Pk "$ROOT" | awk 'NR==2 {print int($4/1024/1024)}')
(( free_gb >= 6 )) || fail "At least 6 GB free disk is required after the model is already present; found ${free_gb} GB"

bash -n scripts/migrate_gigachat31_12gb.sh
python3 -m py_compile \
  scripts/gigachat31_runtime_audit.py \
  scripts/answer_pipeline_audit.py \
  scripts/download_model.py \
  scripts/warm_local_llm.py \
  app/high_risk_verification_patch.py \
  app/market_freshness_patch.py \
  app/services/freshness.py
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

info "Downloading/verifying pinned GigaChat 3.1 Lightning Q4_K_M"
python3 scripts/download_model.py --profile primary --retries 6 --timeout 180
python3 scripts/download_model.py --profile primary --verify-only

info "Applying 12 GiB runtime envelope"
python3 - "$threads" "$batch_threads" <<'PY'
from pathlib import Path
import sys
threads=sys.argv[1]; batch_threads=sys.argv[2]
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
    'X1_LLAMA_THREADS':threads,
    'X1_LLAMA_THREADS_BATCH':batch_threads,
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
    if key not in seen: out.append(f'{key}={value}')
path.write_text('\n'.join(out).rstrip()+'\n','utf-8')
PY
chmod 600 .env

info "Validating resolved GigaChat Compose configuration"
resolved_compose=$(mktemp)
docker compose config > "$resolved_compose"
grep -Fq '/models/GigaChat3.1-10B-A1.8B-q4_K_M.gguf' "$resolved_compose" || { rm -f "$resolved_compose"; fail "Compose did not resolve the GigaChat model file"; }
grep -Fq -- '--cpu-moe' "$resolved_compose" || { rm -f "$resolved_compose"; fail "Compose did not enable llama.cpp CPU-MoE"; }
rm -f "$resolved_compose"

info "Rebuilding application runtime and restarting optimized local inference"
docker compose up -d --build --force-recreate llama app

info "Waiting for GigaChat llama.cpp health"
ready=0
for _ in $(seq 1 90); do
  if docker compose exec -T llama sh -lc "curl -fsS http://127.0.0.1:8080/health >/dev/null" >/dev/null 2>&1; then ready=1; break; fi
  sleep 2
done
[ "$ready" -eq 1 ] || { docker compose logs --tail=160 llama >&2 || true; fail "GigaChat llama.cpp did not become healthy"; }

info "Waiting for application warm-up and health"
app_ready=0
for _ in $(seq 1 90); do
  status=$(docker inspect --format='{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$(docker compose ps -q app)" 2>/dev/null || true)
  if [ "$status" = "healthy" ]; then app_ready=1; break; fi
  sleep 2
done
[ "$app_ready" -eq 1 ] || { docker compose logs --tail=220 app >&2 || true; fail "Application did not become healthy after LLM warm-up"; }

info "Verifying model artifact inside host storage"
python3 scripts/download_model.py --profile primary --verify-only

info "Running live GigaChat 3.1 integration + performance audit"
docker compose exec -T app python -m scripts.gigachat31_runtime_audit

info "Running core answer-pipeline sanity checks"
docker compose exec -T app python -m scripts.answer_pipeline_audit

trap - ERR INT TERM
info "GigaChat 3.1 migration completed successfully (RAM=${ram_gib}GiB, cpu=${cores}, threads=${threads}, context=4096, llama_limit=8g, cpu_moe=on, warmup=on)"
info "Rollback env retained at: $backup"
