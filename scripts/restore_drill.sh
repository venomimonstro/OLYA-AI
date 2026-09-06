#!/usr/bin/env bash
set -euo pipefail
umask 077

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

command -v docker >/dev/null 2>&1 || { echo "docker required" >&2; exit 2; }
docker compose version >/dev/null 2>&1 || { echo "docker compose required" >&2; exit 2; }

BACKUP="${1:-}"
if [ -z "$BACKUP" ]; then
  BACKUP=$(find "$ROOT/backups" -mindepth 1 -maxdepth 1 -type d ! -name '*.partial.*' -printf '%T@ %p\n' 2>/dev/null | sort -nr | awk 'NR==1 {$1=""; sub(/^ /,""); print}')
fi
[ -n "$BACKUP" ] || { echo "no backup snapshot found" >&2; exit 3; }
BACKUP="$(cd "$BACKUP" && pwd)"
[ -f "$BACKUP/SHA256SUMS" ] || { echo "SHA256SUMS missing" >&2; exit 3; }
[ -s "$BACKUP/database.dump" ] || { echo "database.dump missing or empty" >&2; exit 3; }
[ -s "$BACKUP/files.tar" ] || { echo "files.tar missing or empty" >&2; exit 3; }

REPORT="$ROOT/backups/restore-drill-latest.json"
TMP_ROOT="$(mktemp -d -t x1-restore-drill.XXXXXX)"
DB_NAME="x1_restore_drill_$(date -u +%Y%m%d%H%M%S)_$$"
DB_CREATED=0
STARTED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

cleanup() {
  if [ "$DB_CREATED" -eq 1 ]; then
    docker compose exec -T db dropdb -U x1 --if-exists "$DB_NAME" >/dev/null 2>&1 || true
  fi
  rm -rf "$TMP_ROOT"
}
trap cleanup EXIT

(
  cd "$BACKUP"
  sha256sum -c SHA256SUMS
) >/dev/null

# Validate archive paths, reject links/devices and actually extract into an
# isolated temporary directory. This catches corrupt archives and traversal
# payloads without ever writing into the production x1_data volume.
python3 - "$BACKUP/files.tar" "$TMP_ROOT/files" <<'PY'
from __future__ import annotations
import json
import sys
import tarfile
from pathlib import Path, PurePosixPath

archive_path = Path(sys.argv[1])
destination = Path(sys.argv[2])
destination.mkdir(parents=True, exist_ok=True)
file_count = 0
total_bytes = 0
with tarfile.open(archive_path, "r:*") as archive:
    members = archive.getmembers()
    for member in members:
        raw = member.name.replace("\\", "/")
        path = PurePosixPath(raw)
        if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
            raise SystemExit(f"unsafe archive path: {member.name}")
        if member.issym() or member.islnk() or member.isdev() or member.isfifo():
            raise SystemExit(f"unsafe archive member: {member.name}")
        if member.isfile():
            file_count += 1
            total_bytes += int(member.size)
    archive.extractall(destination, filter="data")
print(json.dumps({"files": file_count, "bytes": total_bytes}))
PY

# Real PostgreSQL restore into a disposable database on the same PostgreSQL
# engine used by production. Production x1 is never dropped, truncated or used
# as a restore target.
docker compose exec -T db createdb -U x1 "$DB_NAME"
DB_CREATED=1
cat "$BACKUP/database.dump" | docker compose exec -T db pg_restore -U x1 -d "$DB_NAME" --no-owner --no-privileges

TABLE_COUNT=$(docker compose exec -T db psql -U x1 -d "$DB_NAME" -Atqc "select count(*) from information_schema.tables where table_schema='public';")
ALEMBIC_VERSION=$(docker compose exec -T db psql -U x1 -d "$DB_NAME" -Atqc "select version_num from alembic_version limit 1;" 2>/dev/null || true)
for REQUIRED_TABLE in users projects usage_events background_jobs; do
  PRESENT=$(docker compose exec -T db psql -U x1 -d "$DB_NAME" -Atqc "select count(*) from information_schema.tables where table_schema='public' and table_name='${REQUIRED_TABLE}';")
  [ "$PRESENT" = "1" ] || { echo "restored database missing table: $REQUIRED_TABLE" >&2; exit 4; }
done
[ -n "$ALEMBIC_VERSION" ] || { echo "restored database is not Alembic stamped" >&2; exit 4; }

FINISHED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
python3 - "$REPORT" "$BACKUP" "$STARTED_AT" "$FINISHED_AT" "$TABLE_COUNT" "$ALEMBIC_VERSION" <<'PY'
from __future__ import annotations
import json
import os
import sys
from pathlib import Path

report, backup, started, finished, table_count, alembic_version = sys.argv[1:]
payload = {
    "status": "passed",
    "backup": backup,
    "started_at": started,
    "finished_at": finished,
    "restored_table_count": int(table_count),
    "alembic_version": alembic_version,
}
target = Path(report)
target.parent.mkdir(parents=True, exist_ok=True)
tmp = target.with_suffix(target.suffix + ".tmp")
tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", "utf-8")
os.replace(tmp, target)
print(json.dumps(payload, ensure_ascii=False))
PY
