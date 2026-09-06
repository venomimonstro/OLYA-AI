#!/usr/bin/env bash
set -euo pipefail
umask 077

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

FINAL_DEST="${1:-$ROOT/backups/$(date -u +%Y%m%dT%H%M%SZ)}"
TMP_DEST="${FINAL_DEST}.partial.$$"
DATA_ROOT="${X1_HOST_DATA_ROOT:-$ROOT/data}"

command -v docker >/dev/null || { echo "docker required" >&2; exit 2; }
docker compose version >/dev/null 2>&1 || { echo "docker compose required" >&2; exit 2; }
[ ! -e "$FINAL_DEST" ] || { echo "backup destination already exists: $FINAL_DEST" >&2; exit 2; }
[ -d "$DATA_ROOT" ] || mkdir -p "$DATA_ROOT"
DATA_ROOT="$(cd "$DATA_ROOT" && pwd)"

cleanup() { rm -rf "$TMP_DEST"; }
trap cleanup EXIT
mkdir -p "$TMP_DEST"

# pg_dump is executed after callers have quiesced write-producing application
# services for update backups. A failed dump can never be promoted as complete.
docker compose exec -T db pg_dump -U x1 -d x1 -Fc > "$TMP_DEST/database.dump"
[ -s "$TMP_DEST/database.dump" ] || { echo "database dump is empty" >&2; exit 3; }

# Sprint 39+ uses a host bind as the authoritative /app/data. Archive it directly
# so backups still work while the web application is stopped during a safe
# update. Reject links/devices: the matching restore path is deliberately
# traversal/link safe.
python3 - "$DATA_ROOT" "$TMP_DEST/files.tar" <<'PY'
from __future__ import annotations
import sys, tarfile
from pathlib import Path

root = Path(sys.argv[1]).resolve()
out = Path(sys.argv[2])
with tarfile.open(out, "w") as archive:
    archive.add(root, arcname="data", recursive=True, filter=lambda info: None if (info.issym() or info.islnk() or info.isdev() or info.isfifo()) else info)
PY
[ -s "$TMP_DEST/files.tar" ] || { echo "application data archive is empty" >&2; exit 3; }

[ -f .env ] && cp .env "$TMP_DEST/env.backup"
git rev-parse HEAD > "$TMP_DEST/git-head.txt" 2>/dev/null || true
{
  printf 'created_at=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  printf 'format=x1-backup-v3\n'
  printf 'data_root=%s\n' "$DATA_ROOT"
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
