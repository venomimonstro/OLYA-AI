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

RUNNING_SERVICES="$(docker compose ps --services --filter status=running 2>/dev/null || true)"
was_running() { printf '%s\n' "$RUNNING_SERVICES" | grep -qx "$1"; }
WRITERS_STOPPED=0
SERVICES_RESUMED=0

quiesce_sandbox_containers() {
  local ids
  ids="$({
    docker ps -aq --filter 'label=x1.sandbox.preview=true' 2>/dev/null || true
    docker ps -aq --filter 'label=x1.sandbox.execution=true' 2>/dev/null || true
  } | awk 'NF' | sort -u)"
  [ -z "$ids" ] || docker rm -f $ids >/dev/null
}

resume_services() {
  [ "$SERVICES_RESUMED" -eq 0 ] || return 0
  SERVICES_RESUMED=1
  local failed=0
  set +e
  if was_running sandbox-worker; then docker compose up -d sandbox-worker >/dev/null 2>&1 || failed=1; fi
  if was_running app; then docker compose up -d app >/dev/null 2>&1 || failed=1; fi
  if was_running image-worker; then docker compose --profile images up -d image-worker >/dev/null 2>&1 || failed=1; fi
  set -e
  return "$failed"
}

TMP_ROOT="$(mktemp -d "$ROOT/.x1-restore.XXXXXX")"
CURRENT_ENV_COPY="$(mktemp -t x1-current-env.XXXXXX)"
CURRENT_ENV_EXISTS=0
if [ -f "$ROOT/.env" ]; then
  cp "$ROOT/.env" "$CURRENT_ENV_COPY"
  CURRENT_ENV_EXISTS=1
else
  : > "$CURRENT_ENV_COPY"
fi
chmod 600 "$CURRENT_ENV_COPY"

TEMP_DB="x1_restore_$$"
OLD_DB="x1_rollback_$$"
FAILED_DB="x1_failed_$$"
OLD_DATA="${DATA_ROOT}.rollback-old.$$"
DB_SWAPPED=0
DATA_SWAPPED=0
HAD_OLD_DATA=0
CUTOVER_ACTIVE=0

cleanup() {
  local original_status=$?
  rm -rf "$TMP_ROOT"
  rm -f "$CURRENT_ENV_COPY"
  if [ "$DB_SWAPPED" -eq 0 ]; then
    docker compose exec -T db dropdb -U x1 --if-exists --force "$TEMP_DB" >/dev/null 2>&1 || true
  fi
  if [ "$WRITERS_STOPPED" -eq 1 ] && [ "$CUTOVER_ACTIVE" -eq 0 ]; then
    resume_services || true
  fi
  return "$original_status"
}
trap cleanup EXIT
trap 'exit 130' INT TERM

# Safe application-data preflight. No production state is changed here.
python3 - "$BACKUP/files.tar" "$TMP_ROOT" "${X1_RESTORE_MAX_DATA_BYTES:-0}" <<'PY'
from __future__ import annotations
import shutil, sys, tarfile
from pathlib import Path, PurePosixPath

archive = Path(sys.argv[1])
destination = Path(sys.argv[2])
operator_limit = max(0, int(sys.argv[3] or 0))
destination.mkdir(parents=True, exist_ok=True)
free_bytes = shutil.disk_usage(destination).free
disk_budget = int(free_bytes * 0.90)
max_bytes = min(disk_budget, operator_limit) if operator_limit else disk_budget
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
            if total > max_bytes:
                raise SystemExit(
                    f"backup application data ({total} bytes) exceeds safe restore capacity ({max_bytes} bytes)"
                )
    tf.extractall(destination, filter="data")
restored = destination / "data"
if not restored.is_dir():
    raise SystemExit("backup does not contain data root")
PY

# Restore database into a disposable candidate first. Production x1 remains live.
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
docker compose exec -T db psql -U x1 -d "$TEMP_DB" -Atqc \
  "SELECT count(*) FROM information_schema.tables WHERE table_schema='public' AND table_name='alembic_version'" \
  | grep -qx '1' || { echo "candidate database has no alembic_version table" >&2; exit 4; }

# Stop all components that can write DB/files before atomic cutover.
if was_running app || was_running image-worker || was_running sandbox-worker; then WRITERS_STOPPED=1; fi
docker compose stop app image-worker sandbox-worker >/dev/null 2>&1 || true
quiesce_sandbox_containers
SERVICES_RESUMED=0

rollback_cutover() {
  local reason="$1"
  trap - ERR INT TERM
  set +e
  printf '[X1 restore] ROLLBACK: %s\n' "$reason" >&2
  docker compose stop app image-worker sandbox-worker >/dev/null 2>&1 || true
  quiesce_sandbox_containers >/dev/null 2>&1 || true

  if [ "$DB_SWAPPED" -eq 1 ]; then
    docker compose exec -T db psql -U x1 -d postgres -v ON_ERROR_STOP=1 -c \
      "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname IN ('x1','$OLD_DB') AND pid <> pg_backend_pid();" >/dev/null 2>&1 || true
    docker compose exec -T db psql -U x1 -d postgres -c "ALTER DATABASE x1 RENAME TO $FAILED_DB;" >/dev/null 2>&1 || true
    docker compose exec -T db psql -U x1 -d postgres -c "ALTER DATABASE $OLD_DB RENAME TO x1;" >/dev/null 2>&1 || true
    docker compose exec -T db dropdb -U x1 --if-exists --force "$FAILED_DB" >/dev/null 2>&1 || true
    DB_SWAPPED=0
  fi

  if [ "$DATA_SWAPPED" -eq 1 ]; then
    rm -rf "$DATA_ROOT"
    if [ "$HAD_OLD_DATA" -eq 1 ] && [ -e "$OLD_DATA" ]; then
      mv "$OLD_DATA" "$DATA_ROOT" >/dev/null 2>&1 || true
    else
      mkdir -p "$DATA_ROOT"
      chown -R 10001:10001 "$DATA_ROOT" >/dev/null 2>&1 || true
    fi
    DATA_SWAPPED=0
  fi

  if [ "$CURRENT_ENV_EXISTS" -eq 1 ]; then
    cp "$CURRENT_ENV_COPY" "$ROOT/.env" >/dev/null 2>&1 || true
    chmod 600 "$ROOT/.env" >/dev/null 2>&1 || true
  else
    rm -f "$ROOT/.env"
  fi

  CUTOVER_ACTIVE=0
  SERVICES_RESUMED=0
  resume_services >/dev/null 2>&1 || true
  printf '[X1 restore] Previous database, files and host configuration restored.\n' >&2
  exit 5
}

CUTOVER_ACTIVE=1
trap 'rollback_cutover "restore command failed at line $LINENO"' ERR
trap 'rollback_cutover "restore interrupted"' INT TERM

# Database name swap is fast and reversible until service verification succeeds.
docker compose exec -T db psql -U x1 -d postgres -v ON_ERROR_STOP=1 -c \
  "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname IN ('x1','$TEMP_DB') AND pid <> pg_backend_pid();" >/dev/null
docker compose exec -T db psql -U x1 -d postgres -v ON_ERROR_STOP=1 -c "ALTER DATABASE x1 RENAME TO $OLD_DB;" >/dev/null
if ! docker compose exec -T db psql -U x1 -d postgres -v ON_ERROR_STOP=1 -c "ALTER DATABASE $TEMP_DB RENAME TO x1;" >/dev/null; then
  docker compose exec -T db psql -U x1 -d postgres -v ON_ERROR_STOP=1 -c "ALTER DATABASE $OLD_DB RENAME TO x1;" >/dev/null 2>&1 || true
  echo "candidate database cutover failed; original database name restored" >&2
  exit 4
fi
DB_SWAPPED=1

# Keep old files on the same filesystem until the restored services prove they work.
mkdir -p "$(dirname "$DATA_ROOT")"
if [ -e "$DATA_ROOT" ]; then
  mv "$DATA_ROOT" "$OLD_DATA"
  HAD_OLD_DATA=1
fi
mv "$TMP_ROOT/data" "$DATA_ROOT"
DATA_SWAPPED=1
chown -R 10001:10001 "$DATA_ROOT" 2>/dev/null || true
chmod 750 "$DATA_ROOT" 2>/dev/null || true

# Backups contain application secrets/config, but PostgreSQL role credentials and
# target-node hardware paths are host infrastructure, not database content.
# Restoring an old POSTGRES_PASSWORD onto a fresh cluster would otherwise make the
# app immediately unable to authenticate to the still-current x1 database role.
if [ -f "$BACKUP/env.backup" ]; then
  python3 - "$BACKUP/env.backup" "$CURRENT_ENV_COPY" "$ROOT/.env.restore.$$" "$CURRENT_ENV_EXISTS" <<'PY'
from __future__ import annotations
import os, sys
from pathlib import Path

backup_path, current_path, output_path, current_exists = sys.argv[1:]
backup = Path(backup_path).read_text("utf-8", errors="strict").splitlines()
current_lines = Path(current_path).read_text("utf-8", errors="strict").splitlines() if current_exists == "1" else []

# These values describe the current PostgreSQL cluster/network/hardware. Keep
# them from the target host while restoring all application-level secrets/state.
preserve = {
    "POSTGRES_PASSWORD",
    "X1_DATABASE_URL",
    "X1_HOST_DATA_ROOT",
    "X1_BIND_ADDRESS",
    "X1_PORT",
    "X1_LLAMA_MEMORY_LIMIT",
    "X1_LLAMA_THREADS",
    "X1_LLAMA_THREADS_BATCH",
    "X1_MAX_CONTEXT_TOKENS",
    "X1_DEEP_CONTEXT_TOKENS",
}

def values(lines):
    out = {}
    for line in lines:
        if line and not line.lstrip().startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            out[key] = value
    return out

host = values(current_lines)
result = list(backup)
seen = set()
for idx, line in enumerate(result):
    if not line or line.lstrip().startswith("#") or "=" not in line:
        continue
    key = line.split("=", 1)[0]
    if key in preserve and key in host:
        result[idx] = f"{key}={host[key]}"
        seen.add(key)
for key in sorted(preserve - seen):
    if key in host:
        result.append(f"{key}={host[key]}")
target = Path(output_path)
target.write_text("\n".join(result).rstrip() + "\n", "utf-8")
os.chmod(target, 0o600)
PY
  mv "$ROOT/.env.restore.$$" "$ROOT/.env"
  chmod 600 "$ROOT/.env"
fi

# Restart exactly the services that were running before restore, then prove the
# restored application can open the restored DB before discarding rollback state.
SERVICES_RESUMED=0
resume_services || rollback_cutover "one or more previously running services failed to restart"

if was_running app; then
  app_ready=0
  for _ in $(seq 1 90); do
    if docker compose exec -T app python - <<'PY' >/dev/null 2>&1
from urllib.request import urlopen
urlopen('http://127.0.0.1:8000/health', timeout=3).read()
PY
    then
      if docker compose exec -T app python - <<'PY' >/dev/null 2>&1
from sqlalchemy import text
from app.db import engine
with engine.connect() as connection:
    assert connection.execute(text('select 1')).scalar_one() == 1
PY
      then app_ready=1; break; fi
    fi
    sleep 2
  done
  [ "$app_ready" -eq 1 ] || rollback_cutover "restored app could not become healthy and query PostgreSQL"
fi
if was_running sandbox-worker; then
  docker compose exec -T sandbox-worker python - <<'PY' >/dev/null 2>&1 || rollback_cutover "sandbox worker did not recover after restore"
from urllib.request import urlopen
urlopen('http://127.0.0.1:8090/health', timeout=4).read()
PY
fi
if was_running image-worker; then
  docker compose ps --services --filter status=running | grep -qx image-worker \
    || rollback_cutover "image worker did not recover after restore"
fi

# Only now is rollback state expendable.
docker compose exec -T db dropdb -U x1 --if-exists --force "$OLD_DB" >/dev/null
rm -rf "$OLD_DATA"
DB_SWAPPED=0
DATA_SWAPPED=0
CUTOVER_ACTIVE=0
rm -rf "$TMP_ROOT"
rm -f "$CURRENT_ENV_COPY"
trap - EXIT ERR INT TERM
printf '%s\n' "$BACKUP"
