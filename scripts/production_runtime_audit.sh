#!/usr/bin/env bash
set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT" || exit 2

fail=0
say(){ printf '[OLYA-AUDIT] %s\n' "$*"; }
mark_fail(){ say "FAIL: $*" >&2; fail=1; }

say "Docker Compose status"
docker compose ps || mark_fail "docker compose ps failed"

app_id=$(docker compose ps -q app 2>/dev/null || true)
if [ -z "$app_id" ]; then
  mark_fail "app container is missing"
else
  app_state=$(docker inspect --format='{{.State.Status}}' "$app_id" 2>/dev/null || true)
  app_health=$(docker inspect --format='{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$app_id" 2>/dev/null || true)
  say "app state=${app_state:-unknown} health=${app_health:-unknown}"
  [ "$app_state" = "running" ] || mark_fail "app is not running"
fi

say "Waiting for app /health inside container"
internal_ready=0
for _ in $(seq 1 30); do
  if docker compose exec -T app python - <<'PY' >/tmp/olya-app-health.$$ 2>/dev/null
import json, urllib.request
with urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2) as r:
    body=json.loads(r.read().decode())
    assert r.status == 200 and body.get('status') == 'ok', body
print(json.dumps(body, ensure_ascii=False))
PY
  then
    cat /tmp/olya-app-health.$$
    internal_ready=1
    break
  fi
  sleep 1
done
rm -f /tmp/olya-app-health.$$
[ "$internal_ready" -eq 1 ] || mark_fail "internal app /health did not become ready within 30s"

if [ "$internal_ready" -eq 1 ]; then
  say "Checking app /ready inside container"
  if ! docker compose exec -T app python - <<'PY'
import json, urllib.request, urllib.error
try:
    with urllib.request.urlopen('http://127.0.0.1:8000/ready', timeout=8) as r:
        body=json.loads(r.read().decode())
        print(json.dumps(body, ensure_ascii=False))
        raise SystemExit(0 if r.status == 200 else 2)
except urllib.error.HTTPError as e:
    print(e.read().decode(errors='replace'))
    raise SystemExit(2)
PY
  then
    mark_fail "internal app /ready failed"
  fi
fi

say "Checking published host port"
published=$(docker compose port app 8000 2>/dev/null | tail -n1 || true)
if [ -n "$published" ]; then
  port=${published##*:}
  if python3 - "$port" <<'PY'
import json, sys, urllib.request
port=sys.argv[1]
with urllib.request.urlopen(f'http://127.0.0.1:{port}/health', timeout=4) as r:
    body=json.loads(r.read().decode())
    assert r.status == 200 and body.get('status') == 'ok', body
print(json.dumps({'host_port': port, 'health': body}, ensure_ascii=False))
PY
  then
    say "host port is reachable; if public domain still returns 502, inspect the external reverse proxy/upstream configuration"
  else
    mark_fail "published app port ${published} is not reachable from host"
  fi
else
  mark_fail "docker compose exposes no host port for app:8000"
fi

say "Checking llama.cpp"
if ! docker compose exec -T llama sh -lc 'curl -fsS http://127.0.0.1:8080/health'; then
  mark_fail "llama health failed"
fi
printf '\n'

say "Checking SearXNG"
if ! docker compose exec -T searxng python - <<'PY'
import json, urllib.parse, urllib.request
q=urllib.parse.urlencode({'q':'python','format':'json','engines':'bing'})
with urllib.request.urlopen('http://127.0.0.1:8080/search?'+q, timeout=5) as r:
    data=json.loads(r.read().decode())
print(json.dumps({'results': len(data.get('results') or [])}, ensure_ascii=False))
PY
then
  mark_fail "SearXNG health/search failed"
fi

say "Checking actual production route bindings"
if ! docker compose exec -T app python -m scripts.runtime_binding_audit; then
  mark_fail "production route binding audit failed"
fi

say "Running static search-quality regression"
if ! docker compose exec -T app python -m scripts.search_quality_audit; then
  mark_fail "search quality static audit failed"
fi

say "Running live production search path"
if ! docker compose exec -T app python -m scripts.search_quality_live_probe; then
  mark_fail "search quality live probe failed"
fi

say "Running core answer-pipeline regression"
if ! docker compose exec -T app python -m scripts.answer_pipeline_audit; then
  mark_fail "answer pipeline audit failed"
fi

if [ "$fail" -ne 0 ]; then
  say "One or more checks failed. Recent app logs:"
  docker compose logs --tail=180 app 2>&1 || true
  say "Recent llama logs:"
  docker compose logs --tail=100 llama 2>&1 || true
  say "Recent SearXNG logs:"
  docker compose logs --tail=140 searxng 2>&1 || true
  exit 2
fi

say "PASSED: app, host port, readiness, llama, SearXNG, route bindings, relevance search and answer pipeline are operational"
