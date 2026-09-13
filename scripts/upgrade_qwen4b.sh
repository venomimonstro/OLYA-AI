#!/usr/bin/env bash
set -euo pipefail
umask 077

ROOT="${X1_INSTALL_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
cd "$ROOT"

info(){ printf '[OLYA Qwen4B] %s\n' "$*"; }
fail(){ printf '[OLYA Qwen4B] ERROR: %s\n' "$*" >&2; exit 1; }

if [ "$(id -u)" -ne 0 ]; then
  command -v sudo >/dev/null 2>&1 || fail "run as root or install sudo"
  exec sudo -E env X1_INSTALL_DIR="$ROOT" bash "$0" "$@"
fi

command -v python3 >/dev/null 2>&1 || fail "python3 is required"
command -v docker >/dev/null 2>&1 || fail "docker is required"
command -v curl >/dev/null 2>&1 || fail "curl is required"
docker compose version >/dev/null 2>&1 || fail "Docker Compose v2 is required"
[ -f model-manifest.json ] || fail "model-manifest.json is missing"
[ -f .env ] || { [ -f .env.example ] || fail ".env and .env.example are missing"; cp .env.example .env; }

ram_kb=$(awk '/MemTotal/ {print $2}' /proc/meminfo)
ram_gib=$(python3 - "$ram_kb" <<'PY'
import sys
print(round(int(sys.argv[1]) / 1024 / 1024, 2))
PY
)
cores=$(nproc)
disk_gb=$(df -Pk "$ROOT" | awk 'NR==2 {print int($4/1024/1024)}')
(( ram_kb >= 5*1024*1024 )) || fail "Qwen3-4B starter profile needs at least 5 GiB RAM; detected ${ram_gib} GiB"
(( disk_gb >= 7 )) || fail "at least 7 GB free disk is required for the verified 4B download; found ${disk_gb} GB"

mkdir -p models backups
backup="backups/env-before-qwen4b-$(date -u +%Y%m%dT%H%M%SZ).env"
cp -p .env "$backup"
chmod 600 "$backup"
info "Environment backup: $backup"

threads=$cores
(( threads > 3 )) && threads=3
(( threads < 2 )) && threads=2

python3 - "$threads" <<'PY'
from pathlib import Path
import sys

path = Path('.env')
lines = path.read_text('utf-8').splitlines()
threads = int(sys.argv[1])

def setv(key: str, value: str | int) -> None:
    global lines
    prefix = key + '='
    out = []
    found = False
    for line in lines:
        if line.startswith(prefix):
            if not found:
                out.append(prefix + str(value))
                found = True
        else:
            out.append(line)
    if not found:
        out.append(prefix + str(value))
    lines = out

# Qwen3-4B Q4_K_M is the pinned quality floor for the small CPU/RAM node.
setv('X1_LLAMA_MODEL_NAME', 'Qwen3-4B-Q4_K_M')
setv('X1_LLAMA_MODEL_FILE', 'Qwen3-4B-Q4_K_M.gguf')
setv('X1_LLAMA_BASE_URL', 'http://llama:8080')
setv('X1_SERVER_OPTIMIZATION_PROFILE', 'starter_6gb')

# Keep the 4B model resident without starving PostgreSQL/app/OS on a 6 GiB host.
setv('X1_LLAMA_MEMORY_LIMIT', '3200m')
setv('X1_MAX_CONTEXT_TOKENS', '4096')
setv('X1_DEEP_CONTEXT_TOKENS', '4096')
setv('X1_LLAMA_THREADS', threads)
setv('X1_LLAMA_THREADS_BATCH', threads)
setv('X1_MAX_CONCURRENT_GENERATIONS', '1')
setv('X1_MAX_QUEUE_SIZE', '16')
setv('X1_INFERENCE_MAX_QUEUED_PER_PRINCIPAL', '1')
setv('X1_INFERENCE_QUEUE_TIMEOUT_SECONDS', '120')

# 4B is slower than 2B on CPU. Do not kill a valid long answer prematurely.
setv('X1_DEFAULT_MAX_OUTPUT_TOKENS', '900')
setv('X1_REQUEST_TIMEOUT_SECONDS', '300')

# Preserve RAM for inference. These can be moved to remote workers after a server upgrade.
setv('X1_PROJECT_SANDBOX_BACKEND', 'disabled')
setv('X1_DOCUMENT_RENDER_BACKEND', 'disabled')
setv('X1_IMAGE_BACKEND', 'disabled')
setv('X1_IMAGE_EDIT_BACKEND', 'disabled')
setv('X1_DB_MEMORY_LIMIT_MB', '384')
setv('X1_SEARX_MEMORY_LIMIT_MB', '256')
setv('X1_APP_MEMORY_LIMIT_MB', '768')
setv('X1_DATABASE_POOL_SIZE', '3')
setv('X1_DATABASE_MAX_OVERFLOW', '1')

path.write_text('\n'.join(lines).rstrip() + '\n', 'utf-8')
PY
chmod 600 .env

app_port=$(awk -F= '$1=="X1_PORT"{print $2}' .env | tail -n1 | tr -d '[:space:]')
app_port=${app_port:-8000}

info "Downloading and SHA-256 verifying pinned Qwen3-4B Q4_K_M (~2.5 GB)"
python3 scripts/download_model.py --profile primary
python3 scripts/download_model.py --profile primary --verify-only >/dev/null

info "Building app with the current Qwen3-4B quality/runtime code"
docker compose build app
info "Ensuring PostgreSQL and private search are available"
docker compose up -d db searxng

info "Recreating local inference with the 4B model"
docker compose --profile inference pull llama >/dev/null
docker compose --profile inference up -d --force-recreate llama

info "Waiting for Qwen3-4B health"
for _ in $(seq 1 120); do
  if docker compose exec -T llama sh -lc 'curl -fsS http://127.0.0.1:8080/health >/dev/null' 2>/dev/null; then
    break
  fi
  sleep 2
done
docker compose exec -T llama sh -lc 'curl -fsS http://127.0.0.1:8080/health >/dev/null' >/dev/null 2>&1 || fail "Qwen3-4B did not become healthy"

info "Recreating app so it receives the new model and timeout settings"
docker compose up -d --force-recreate app
for _ in $(seq 1 90); do
  if curl -fsS "http://127.0.0.1:${app_port}/health" >/dev/null 2>&1; then
    break
  fi
  sleep 2
done
curl -fsS "http://127.0.0.1:${app_port}/health" >/dev/null 2>&1 || fail "OLYA AI app did not become healthy"

info "Running a real non-thinking inference smoke test"
docker compose exec -T app python - <<'PY'
import json
import urllib.request

payload = json.dumps({
    "model": "local",
    "messages": [{"role": "user", "content": "Ответь ровно одним словом: работает"}],
    "max_tokens": 24,
    "stream": False,
    "temperature": 0.7,
    "top_p": 0.8,
    "top_k": 20,
    "min_p": 0.0,
    "presence_penalty": 1.5,
    "chat_template_kwargs": {"enable_thinking": False},
}).encode()
req = urllib.request.Request(
    "http://llama:8080/v1/chat/completions",
    data=payload,
    headers={"Content-Type": "application/json"},
    method="POST",
)
with urllib.request.urlopen(req, timeout=90) as response:
    data = json.loads(response.read().decode())
text = (((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
if not text:
    raise SystemExit("empty inference response")
print("Qwen3-4B smoke answer:", text[:160])
PY

info "Qwen3-4B is active. RAM=${ram_gib} GiB, CPU=${cores}, llama threads=${threads}, context=4096, one inference slot."
info "Old model files were intentionally left in models/ for manual rollback; remove them later only after confirming production stability."
