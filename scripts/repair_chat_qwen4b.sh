#!/usr/bin/env bash
set -euo pipefail
umask 077

ROOT="${X1_INSTALL_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
cd "$ROOT"

info(){ printf '[OLYA chat repair] %s\n' "$*"; }
fail(){ printf '[OLYA chat repair] ERROR: %s\n' "$*" >&2; exit 1; }

if [ "$(id -u)" -ne 0 ]; then
  command -v sudo >/dev/null 2>&1 || fail "run as root or install sudo"
  exec sudo -E env X1_INSTALL_DIR="$ROOT" bash "$0" "$@"
fi

command -v python3 >/dev/null 2>&1 || fail "python3 is required"
command -v docker >/dev/null 2>&1 || fail "docker is required"
command -v curl >/dev/null 2>&1 || fail "curl is required"
docker compose version >/dev/null 2>&1 || fail "Docker Compose v2 is required"
[ -f .env ] || fail ".env is missing"
[ -f model-manifest.json ] || fail "model-manifest.json is missing"

info "Normalizing Qwen3-4B runtime settings"
python3 - <<'PY'
from pathlib import Path
path=Path('.env')
lines=path.read_text('utf-8').splitlines()

def setv(key,value):
    global lines
    prefix=key+'='
    out=[]; found=False
    for line in lines:
        if line.startswith(prefix):
            if not found:
                out.append(prefix+str(value)); found=True
        else:
            out.append(line)
    if not found:
        out.append(prefix+str(value))
    lines=out

setv('X1_LLAMA_MODEL_NAME','Qwen3-4B-Q4_K_M')
setv('X1_LLAMA_MODEL_FILE','Qwen3-4B-Q4_K_M.gguf')
setv('X1_LLAMA_BASE_URL','http://llama:8080')
setv('X1_MAX_CONTEXT_TOKENS','4096')
setv('X1_DEEP_CONTEXT_TOKENS','4096')
setv('X1_LLAMA_MEMORY_LIMIT','3200m')
setv('X1_MAX_CONCURRENT_GENERATIONS','1')
setv('X1_DEFAULT_MAX_OUTPUT_TOKENS','900')
setv('X1_REQUEST_TIMEOUT_SECONDS','300')
path.write_text('\n'.join(lines).rstrip()+'\n','utf-8')
PY
chmod 600 .env

info "Verifying pinned Qwen3-4B GGUF"
if ! python3 scripts/download_model.py --profile primary --verify-only >/dev/null 2>&1; then
  info "Pinned model is missing or invalid; downloading verified artifact"
  python3 scripts/download_model.py --profile primary
fi

info "Starting database and search"
docker compose up -d db searxng

info "Starting/recreating local Qwen3-4B inference"
docker compose up -d --force-recreate llama
for _ in $(seq 1 120); do
  if docker compose exec -T llama sh -lc 'curl -fsS http://127.0.0.1:8080/health >/dev/null' 2>/dev/null; then
    break
  fi
  sleep 2
done
if ! docker compose exec -T llama sh -lc 'curl -fsS http://127.0.0.1:8080/health >/dev/null' >/dev/null 2>&1; then
  docker compose logs --tail=160 llama >&2 || true
  fail "llama.cpp/Qwen3-4B did not become healthy"
fi

info "Building/recreating application with current code"
docker compose build app
docker compose up -d --force-recreate app
for _ in $(seq 1 90); do
  if curl -fsS "http://127.0.0.1:${X1_PORT:-8000}/health" >/dev/null 2>&1; then
    break
  fi
  sleep 2
done
if ! curl -fsS "http://127.0.0.1:${X1_PORT:-8000}/health" >/dev/null 2>&1; then
  docker compose logs --tail=160 app >&2 || true
  fail "OLYA AI application did not become healthy"
fi

info "Checking app -> llama network connectivity"
docker compose exec -T app python - <<'PY'
from urllib.request import urlopen
body=urlopen('http://llama:8080/health',timeout=8).read().decode('utf-8','replace')
print(body[:200])
PY

info "Running direct Qwen3-4B inference smoke test"
docker compose exec -T app python - <<'PY'
import json
from urllib.request import Request, urlopen
payload=json.dumps({
  'model':'local',
  'messages':[{'role':'user','content':'Ответь одним словом: работает'}],
  'max_tokens':32,
  'stream':False,
  'temperature':0.7,
  'top_p':0.8,
  'top_k':20,
  'min_p':0.0,
  'presence_penalty':1.5,
  'chat_template_kwargs':{'enable_thinking':False},
}).encode()
req=Request('http://llama:8080/v1/chat/completions',data=payload,headers={'Content-Type':'application/json'},method='POST')
with urlopen(req,timeout=120) as response:
    data=json.loads(response.read().decode())
text=(((data.get('choices') or [{}])[0].get('message') or {}).get('content') or '').strip()
if not text:
    raise SystemExit('empty inference response')
print('Qwen3-4B:',text[:200])
PY

info "Chat runtime is healthy"
docker compose ps app llama db searxng
