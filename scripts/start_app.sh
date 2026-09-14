#!/usr/bin/env sh
set -eu

# Database migrations are a hard startup dependency: serving against an old
# schema is unsafe. LLM warm-up is not. The HTTP service must come up even if
# the model is temporarily slow/unavailable; chat already has its own bounded
# inference failure handling.
alembic upgrade head

# Long-form generation is opt-in at the router level, but the process-wide
# request deadline still needs enough headroom for ~10k-character articles on
# the 6-thread CPU profile. A larger ceiling does not slow normal requests; they
# still finish immediately when inference completes.
case "${X1_REQUEST_TIMEOUT_SECONDS:-180}" in
  ''|*[!0-9]*) export X1_REQUEST_TIMEOUT_SECONDS=360 ;;
  *)
    if [ "${X1_REQUEST_TIMEOUT_SECONDS:-180}" -lt 360 ]; then
      export X1_REQUEST_TIMEOUT_SECONDS=360
    fi
    ;;
esac

(
  python -m scripts.warm_local_llm >/tmp/olya-llm-warmup.log 2>&1 || true
) &

exec uvicorn app.main:app \
  --host 0.0.0.0 \
  --port 8000 \
  --workers 1 \
  --limit-concurrency "${X1_HTTP_LIMIT_CONCURRENCY:-32}" \
  --backlog "${X1_HTTP_BACKLOG:-512}" \
  --timeout-keep-alive "${X1_HTTP_KEEPALIVE_SECONDS:-5}"
