#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

info(){ printf '[OLYA-UI] %s\n' "$*"; }
fail(){ printf '[OLYA-UI] ERROR: %s\n' "$*" >&2; exit 1; }

info "Preflight Python and shell syntax"
python3 -m py_compile \
  app/api/routes/conversations.py \
  app/api/routes/memory.py \
  app/api/routes/admin_chat_observer.py \
  app/api/routes/smart_chat.py \
  app/services/clean_web.py \
  app/services/live_structured_facts.py \
  app/services/long_term_memory.py \
  app/services/memory_write_through.py \
  app/services/project_context.py \
  app/services/response_strategy.py \
  app/inference/router.py \
  app/utility_chat.py \
  app/workspace_recovery_controls.py \
  app/workspace_chat_library_v1.py \
  app/task_solver_user_ui.py \
  app/admin_ui.py \
  app/admin_users_ui.py \
  app/admin_chats_ui.py \
  scripts/client_product_audit.py \
  scripts/latency_path_audit.py
bash -n scripts/start_app.sh
docker compose config --quiet

info "Rebuilding application only; llama.cpp/search/database stay running"
docker compose up -d --build --force-recreate app

info "Waiting for application health"
ready=0
for _ in $(seq 1 75); do
  if docker compose exec -T app python - <<'PY' >/dev/null 2>&1
import urllib.request
with urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2) as response:
    assert response.status == 200
PY
  then ready=1; break; fi
  sleep 1
done
if [ "$ready" -ne 1 ]; then
  docker compose logs --tail=220 app >&2 || true
  fail "Application did not become healthy"
fi

info "Running latency-aware answer path audit"
docker compose exec -T app python -m scripts.latency_path_audit

info "Running focused client/admin/memory audit"
docker compose exec -T app python -m scripts.client_product_audit

info "Running broad product route/UI audit"
docker compose exec -T app python -m scripts.product_surface_audit

info "PASSED: fast answers, search routing, workspace, memory and owner surfaces are registered"
