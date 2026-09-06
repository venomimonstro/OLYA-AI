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
TEMP_DB="x1_restore_$$"
OLD_DB="x1_rollback_$$"
FAILED_DB="x1_failed_$$"
DB_SWAPPED=0
cleanup() {
  rm -rf "$TMP_ROOT"
  if [ "$DB_SWAPPED" -eq 0 ]; then
    docker compose exec -T db dropdb -U x1 --if-exists --force "$TEMP_DB" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

python3 - "$BACKUP/files.tar" "$TMP_ROOT" <<'PY'
from __future__ import annotations
import sys, tarfile
from pathlib import Path, PurePosixPath

archive = Path(sys.argv[1])
destination = Path(sys.argv[2])
with tarfile.open(archive, "r:*") as tf:
    total = 0
    for member in tf.getmembers():
        path = PurePosixPath(member.name.replace("\\", "/"))
        if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
            raise SystemExit(f"unsafe archive path: {member.name}")
        if member.issym() or member.islnk() or member.isdev() or member.isfifo():
            raise SystemExit(f"unsafe archive member: {member.name}")
        if member.isfile():
            total += max(0, int(member.size))
            if total > 20 * 1024 * 1024 * 1024:
                raise SystemExit("backup application data exceeds restore safety limit")
    tf.extractall(destination, filter="data")
restored = destination / "data"
if not restored.is_dir():
    raise SystemExit("backup does not contain data root")
PY

# Build a fully restored candidate database while the original x1 database is
# still intact. A broken dump therefore cannot destroy the currently working DB.
docker compose up -d db >/dev/null
for _ in $(seq 1 60); do
  docker compose exec -T db pg_isready -U x1 -d x1 >/dev/null 2>&1 && break
  sleep 1
done
docker compose exec -T db pg_isready -U x1 -d x1 >/dev/null 2>&1 || { echo "database unavailable" >&2; exit 4; }
docker compose exec -T db dropdb -U x1 --if-exists --force "$TEMP_DB" >/dev/null
docker compose exec -T db createdb -U x1 "$TEMP_DB"
if ! cat "$BACKUP/database.dump" | docker compose exec -T db pg_restore -U x1 -d "$TEMP_DB" --single-transaction --no-owner --no-privileges; then
  docker compose exec -T db dropdb -U x1 --if-exists --force "$TEMP_DB" >/dev/null 2>&1 || true
  echo "database restore into candidate DB failed; original database is untouched" >&2
  exit 4
fi
# Sanity check: every production X1 backup must contain its migration stamp.
docker compose exec -T db psql -U x1 -d "$TEMP_DB" -Atqc \
  "SELECT count(*) FROM information_schema.tables WHERE table_schema='public' AND table_name='alembic_version'" \
  | grep -qx '1' || { echo "candidate database has no alembic_version table" >&2; exit 4; }

# Stop every service that can mutate DB/files before the atomic-ish cutover.
docker compose stop app image-worker sandbox-worker >/dev/null 2>&1 || true

# Database rename is metadata-only. Keep OLD_DB until file replacement is also
# complete so the whole restore can still roll back.
docker compose exec -T db psql -U x1 -d postgres -v ON_ERROR_STOP=1 -c \
  "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname IN ('x1','$TEMP_DB') AND pid <> pg_backend_pid();" >/dev/null
docker compose exec -T db psql -U x1 -d postgres -v ON_ERROR_STOP=1 -c "ALTER DATABASE x1 RENAME TO $OLD_DB;" >/dev/null
docker compose exec -T db psql -U x1 -d postgres -v ON_ERROR_STOP=1 -c "ALTER DATABASE $TEMP_DB RENAME TO x1;" >/dev/null
DB_SWAPPED=1

mkdir -p "$(dirname "$DATA_ROOT")"
OLD_DATA="${DATA_ROOT}.rollback-old.$$"
if [ -e "$DATA_ROOT" ]; then mv "$DATA_ROOT" "$OLD_DATA"; fi
if ! mv "$TMP_ROOT/data" "$DATA_ROOT"; then
  rm -rf "$DATA_ROOT"
  [ -e "$OLD_DATA" ] && mv "$OLD_DATA" "$DATA_ROOT"
  # Revert DB metadata swap because file cutover failed.
  docker compose exec -T db psql -U x1 -d postgres -v ON_ERROR_STOP=1 -c \
    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='x1' AND pid <> pg_backend_pid();" >/dev/null 2>&1 || true
  docker compose exec -T db psql -U x1 -d postgres -v ON_ERROR_STOP=1 -c "ALTER DATABASE x1 RENAME TO $FAILED_DB;" >/dev/null 2>&1 || true
  docker compose exec -T db psql -U x1 -d postgres -v ON_ERROR_STOP=1 -c "ALTER DATABASE $OLD_DB RENAME TO x1;" >/dev/null 2>&1 || true
  docker compose exec -T db dropdb -U x1 --if-exists --force "$FAILED_DB" >/dev/null 2>&1 || true
  DB_SWAPPED=0
  echo "application-data cutover failed; original database/files restored" >&2
  exit 4
fi
chown -R 10001:10001 "$DATA_ROOT" 2>/dev/null || true
chmod 750 "$DATA_ROOT" 2>/dev/null || true
rm -rf "$OLD_DATA"

# Only after DB+files succeed, restore the configuration captured with the same
# snapshot. This matters for automatic code rollback across configuration schema
# changes. The current backup format always includes env.backup.
if [ -f "$BACKUP/env.backup" ]; then
  cp "$BACKUP/env.backup" "$ROOT/.env.restore.$$"
  chmod 600 "$ROOT/.env.restore.$$"
  mv "$ROOT/.env.restore.$$" "$ROOT/.env"
fi

# New data is committed. The old DB is no longer needed.
docker compose exec -T db dropdb -U x1 --if-exists --force "$OLD_DB" >/dev/null
DB_SWAPPED=0
trap - EXIT
rm -rf "$TMP_ROOT"
printf '%s\n' "$BACKUP"
