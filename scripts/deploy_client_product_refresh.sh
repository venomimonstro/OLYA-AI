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
  app/services/structured_facts.py \
  app/services/live_structured_facts.py \
  app/services/long_term_memory.py \
  app/services/memory_write_through.py \
  app/services/project_context.py \
  app/services/response_strategy.py \
  app/inference/router.py \
  app/utility_chat.py \
  app/workspace_client_v4.py \
  app/workspace_recovery_controls.py \
  app/workspace_chat_library_v1.py \
  app/task_solver_user_ui.py \
  app/admin_ui.py \
  app/admin_users_ui.py \
  app/admin_chats_ui.py \
  scripts/warm_local_llm.py \
  scripts/client_product_audit.py \
  scripts/latency_path_audit.py \
  scripts/exact_fastpath_audit.py \
  scripts/workspace_stability_audit.py \
  scripts/web_prompt_budget_audit.py \
  scripts/answer_quality_lint.py \
  scripts/real_user_scenarios.py \
  scripts/real_user_routing_audit.py \
  scripts/real_user_live_simulation_100.py \
  scripts/real_user_population_10000.py \
  scripts/real_user_routing_audit_10000.py \
  scripts/real_user_live_simulation_10000.py \
  scripts/real_user_live_weighted_10000.py \
  scripts/real_user_simulation_orchestrator.py
bash -n scripts/start_app.sh
bash -n scripts/run_real_user_simulation_100.sh
bash -n scripts/run_real_user_simulation_10000.sh
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

info "Running workspace browser-stability regression"
docker compose exec -T app python -m scripts.workspace_stability_audit

info "Running compact web-prompt latency regression"
docker compose exec -T app python -m scripts.web_prompt_budget_audit

info "Warming the exact fast-chat prompt prefix"
docker compose exec -T app python -m scripts.warm_local_llm

info "Running exact deterministic fast-path audit"
docker compose exec -T app python -m scripts.exact_fastpath_audit

info "Running latency-aware answer path audit"
docker compose exec -T app python -m scripts.latency_path_audit

info "Running canonical 100-user routing regression"
docker compose exec -T app python -m scripts.real_user_routing_audit

info "Running full 10,000-session persona/style routing simulation"
docker compose exec -T app python -m scripts.real_user_routing_audit_10000

info "Running focused client/admin/memory audit"
docker compose exec -T app python -m scripts.client_product_audit

info "Running broad product route/UI audit"
docker compose exec -T app python -m scripts.product_surface_audit

info "PASSED: workspace stability, compact web evidence, exact fast paths, 10,000-user routing, memory and owner surfaces are registered"
