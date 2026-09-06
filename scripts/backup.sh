#!/usr/bin/env bash
set -euo pipefail
umask 077

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

FINAL_DEST="${1:-$ROOT/backups/$(date -u +%Y%m%dT%H%M%SZ)}"
TMP_DEST="${FINAL_DEST}.partial.$$"

command -v docker >/dev/null || { echo "docker required" >&2; exit 2; }
docker compose version >/dev/null 2>&1 || { echo "docker compose required" >&2; exit 2; }
[ ! -e "$FINAL_DEST" ] || { echo "backup destination already exists: $FINAL_DEST" >&2; exit 2; }

cleanup() {
  rm -rf "$TMP_DEST"
}
trap cleanup EXIT
mkdir -p "$TMP_DEST"

# Database snapshot. A failed pg_dump terminates the whole backup because of
# pipefail/set -e; a partial backup is never promoted to FINAL_DEST.
docker compose exec -T db pg_dump -U x1 -d x1 -Fc > "$TMP_DEST/database.dump"
[ -s "$TMP_DEST/database.dump" ] || { echo "database dump is empty" >&2; exit 3; }

# Application artifacts live in the named x1_data volume mounted at /app/data.
# Stream them out of the running app container instead of looking for stale host
# directories that are not authoritative in production.
docker compose exec -T app python -c '
import sys, tarfile
from pathlib import Path
root = Path("/app/data")
root.mkdir(parents=True, exist_ok=True)
with tarfile.open(fileobj=sys.stdout.buffer, mode="w|") as archive:
    archive.add(root, arcname="data", recursive=True)
' > "$TMP_DEST/files.tar"
[ -s "$TMP_DEST/files.tar" ] || { echo "application data archive is empty" >&2; exit 3; }

[ -f .env ] && cp .env "$TMP_DEST/env.backup"
git rev-parse HEAD > "$TMP_DEST/git-head.txt" 2>/dev/null || true
{
  printf 'created_at=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  printf 'format=x1-backup-v2\n'
} > "$TMP_DEST/METADATA"

(
  cd "$TMP_DEST"
  for file in database.dump files.tar env.backup git-head.txt METADATA; do
    [ -f "$file" ] && sha256sum "$file"
  done > SHA256SUMS
  sha256sum -c SHA256SUMS >/dev/null
)

mkdir -p "$(dirname "$FINAL_DEST")"
mv "$TMP_DEST" "$FINAL_DEST"
trap - EXIT
printf '%s\n' "$FINAL_DEST"
