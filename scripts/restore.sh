#!/usr/bin/env bash
set -euo pipefail
umask 077

ROOT="${X1_INSTALL_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
cd "$ROOT"
BACKUP="${1:-}"
[ -n "$BACKUP" ] || { echo "usage: bash scripts/restore.sh BACKUP_DIR" >&2; exit 2; }
BACKUP="$(cd "$BACKUP" && pwd)"
DATA_ROOT="${X1_HOST_DATA_ROOT:-$ROOT/data}"

command -v docker >/dev/null || { echo "docker required" >&2; exit 2; }
docker compose version >/dev/null 2>&1 || { echo "docker compose required" >&2; exit 2; }
[ -f "$BACKUP/SHA256SUMS" ] || { echo "SHA256SUMS missing" >&2; exit 3; }
[ -s "$BACKUP/database.dump" ] || { echo "database.dump missing" >&2; exit 3; }
[ -s "$BACKUP/files.tar" ] || { echo "files.tar missing" >&2; exit 3; }
(
  cd "$BACKUP"
  sha256sum -c SHA256SUMS >/dev/null
)

TMP_ROOT="$(mktemp -d "$ROOT/.x1-restore.XXXXXX")"
cleanup() { rm -rf "$TMP_ROOT"; }
trap cleanup EXIT

python3 - "$BACKUP/files.tar" "$TMP_ROOT" <<'PY'
from __future__ import annotations
import sys, tarfile
from pathlib import Path, PurePosixPath

archive = Path(sys.argv[1])
destination = Path(sys.argv[2])
with tarfile.open(archive, "r:*") as tf:
    for member in tf.getmembers():
        path = PurePosixPath(member.name.replace("\\", "/"))
        if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
            raise SystemExit(f"unsafe archive path: {member.name}")
        if member.issym() or member.islnk() or member.isdev() or member.isfifo():
            raise SystemExit(f"unsafe archive member: {member.name}")
    tf.extractall(destination, filter="data")
restored = destination / "data"
if not restored.is_dir():
    raise SystemExit("backup does not contain data root")
PY

# Stop every service that can mutate DB/files before destructive restoration.
docker compose stop app image-worker sandbox-worker >/dev/null 2>&1 || true

# Restore PostgreSQL from a custom-format dump. --force disconnects stale app
# sessions if a failed update left any behind.
docker compose exec -T db dropdb -U x1 --if-exists --force x1
docker compose exec -T db createdb -U x1 x1
cat "$BACKUP/database.dump" | docker compose exec -T db pg_restore -U x1 -d x1 --no-owner --no-privileges

# Replace bind-data by rename on the same filesystem, keeping a short-lived local
# fallback until the new tree is in place.
mkdir -p "$(dirname "$DATA_ROOT")"
OLD_DATA="${DATA_ROOT}.rollback-old.$$"
if [ -e "$DATA_ROOT" ]; then mv "$DATA_ROOT" "$OLD_DATA"; fi
if mv "$TMP_ROOT/data" "$DATA_ROOT"; then
  rm -rf "$OLD_DATA"
else
  rm -rf "$DATA_ROOT"
  [ -e "$OLD_DATA" ] && mv "$OLD_DATA" "$DATA_ROOT"
  exit 4
fi
chown -R 10001:10001 "$DATA_ROOT" 2>/dev/null || true
chmod 750 "$DATA_ROOT" 2>/dev/null || true

printf '%s\n' "$BACKUP"
