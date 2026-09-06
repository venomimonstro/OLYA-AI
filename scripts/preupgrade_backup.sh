#!/usr/bin/env bash
set -euo pipefail
umask 077

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

[ -f .env ] || { echo "[X1] no existing .env; pre-upgrade backup not required"; exit 0; }
command -v docker >/dev/null 2>&1 || { echo "[X1] docker required for pre-upgrade database snapshot" >&2; exit 2; }
docker compose version >/dev/null 2>&1 || { echo "[X1] Docker Compose v2 required" >&2; exit 2; }

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
FINAL_DEST="${1:-$ROOT/backups/pre-upgrade-$STAMP}"
TMP_DEST="${FINAL_DEST}.partial.$$"
[ ! -e "$FINAL_DEST" ] || { echo "[X1] pre-upgrade destination exists: $FINAL_DEST" >&2; exit 2; }

cleanup() { rm -rf "$TMP_DEST"; }
trap cleanup EXIT
mkdir -p "$TMP_DEST"

# Start only PostgreSQL if needed. This never starts/migrates the application.
docker compose up -d db >/dev/null
for _ in $(seq 1 60); do
  docker compose exec -T db pg_isready -U x1 -d x1 >/dev/null 2>&1 && break
  sleep 1
done
docker compose exec -T db pg_isready -U x1 -d x1 >/dev/null 2>&1 || {
  echo "[X1] PostgreSQL unavailable; refusing an upgrade without a database snapshot" >&2
  exit 3
}

docker compose exec -T db pg_dump -U x1 -d x1 -Fc > "$TMP_DEST/database.dump"
[ -s "$TMP_DEST/database.dump" ] || { echo "[X1] pre-upgrade database dump is empty" >&2; exit 3; }

# Sprint 39+ uses host data as the authoritative application-data root. Archive
# it directly, so backup remains possible even when the previous app image no
# longer starts. Refuse symlinks: they could make a backup escape the data root.
python3 - "$ROOT/data" "$TMP_DEST/files.tar" <<'PY'
from __future__ import annotations
import os, sys, tarfile
from pathlib import Path
root = Path(sys.argv[1]).resolve()
out = Path(sys.argv[2]).resolve()
root.mkdir(parents=True, exist_ok=True)
for path in root.rglob('*'):
    if path.is_symlink():
        raise SystemExit(f"refusing symlink in X1 data root: {path.relative_to(root)}")
with tarfile.open(out, 'w') as archive:
    archive.add(root, arcname='data', recursive=True)
with out.open('rb') as handle:
    os.fsync(handle.fileno())
PY
[ -s "$TMP_DEST/files.tar" ] || { echo "[X1] pre-upgrade data archive is empty" >&2; exit 3; }

cp .env "$TMP_DEST/env.backup"
git rev-parse HEAD > "$TMP_DEST/git-head.txt" 2>/dev/null || true
{
  printf 'created_at=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  printf 'format=x1-pre-upgrade-v1\n'
  printf 'purpose=restore-before-schema-or-code-upgrade\n'
} > "$TMP_DEST/METADATA"

(
  cd "$TMP_DEST"
  sha256sum database.dump files.tar env.backup git-head.txt METADATA > SHA256SUMS
  sha256sum -c SHA256SUMS >/dev/null
)

mkdir -p "$(dirname "$FINAL_DEST")"
mv "$TMP_DEST" "$FINAL_DEST"
trap - EXIT
printf '%s\n' "$FINAL_DEST"
