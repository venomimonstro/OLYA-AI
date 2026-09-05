#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"; cd "$ROOT"
DEST="${1:-$ROOT/backups/$(date -u +%Y%m%dT%H%M%SZ)}"; mkdir -p "$DEST"
command -v docker >/dev/null || { echo "docker required" >&2; exit 2; }
docker compose exec -T db pg_dump -U x1 -d x1 -Fc > "$DEST/database.dump"
items=(); for d in data/files data/documents data/code_workspaces data/project_runtimes data/images; do [ -e "$d" ] && items+=("$d"); done
if ((${#items[@]})); then tar -C "$ROOT" -cf "$DEST/files.tar" "${items[@]}"; fi
[ -f .env ] && cp .env "$DEST/env.backup"
git rev-parse HEAD > "$DEST/git-head.txt" 2>/dev/null || true
(cd "$DEST" && for f in database.dump files.tar env.backup git-head.txt; do [ -f "$f" ] && sha256sum "$f"; done > SHA256SUMS)
echo "$DEST"
