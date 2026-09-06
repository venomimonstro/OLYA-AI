#!/usr/bin/env bash
set -euo pipefail
umask 077

ROOT="${X1_INSTALL_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
cd "$ROOT"

FINAL_DEST="${1:-$ROOT/backups/$(date -u +%Y%m%dT%H%M%SZ)}"
TMP_DEST="${FINAL_DEST}.partial.$$"
DATA_ROOT="${X1_HOST_DATA_ROOT:-$ROOT/data}"

command -v docker >/dev/null || { echo "docker required" >&2; exit 2; }
docker compose version >/dev/null 2>&1 || { echo "docker compose required" >&2; exit 2; }
[ ! -e "$FINAL_DEST" ] || { echo "backup destination already exists: $FINAL_DEST" >&2; exit 2; }
[ -d "$DATA_ROOT" ] || mkdir -p "$DATA_ROOT"
DATA_ROOT="$(cd "$DATA_ROOT" && pwd)"

# DB rows and files form one logical state. A pg_dump followed by a filesystem
# archive while requests are writing can restore dangling DB/file references.
# Quiesce only write-producing services; PostgreSQL and llama.cpp stay alive.
# During update.sh these writers are already stopped, so this helper will not
# unexpectedly restart them.
RUNNING_SERVICES="$(docker compose ps --services --filter status=running 2>/dev/null || true)"
was_running() { printf '%s\n' "$RUNNING_SERVICES" | grep -qx "$1"; }
WRITERS_QUIESCED=0
WRITERS_RESUMED=0

resume_writers() {
  [ "$WRITERS_RESUMED" -eq 0 ] || return 0
  WRITERS_RESUMED=1
  set +e
  if was_running sandbox-worker; then
    docker compose up -d sandbox-worker >/dev/null 2>&1
  fi
  if was_running app; then
    docker compose up -d app >/dev/null 2>&1
  fi
  if was_running image-worker; then
    docker compose --profile images up -d image-worker >/dev/null 2>&1
  fi
  set -e
}

cleanup() {
  rm -rf "$TMP_DEST"
  if [ "$WRITERS_QUIESCED" -eq 1 ]; then
    resume_writers
  fi
}
trap cleanup EXIT INT TERM

if was_running app || was_running image-worker || was_running sandbox-worker; then
  WRITERS_QUIESCED=1
  docker compose stop app image-worker sandbox-worker >/dev/null 2>&1 || true
fi

mkdir -p "$TMP_DEST"

docker compose exec -T db pg_dump -U x1 -d x1 -Fc > "$TMP_DEST/database.dump"
[ -s "$TMP_DEST/database.dump" ] || { echo "database dump is empty" >&2; exit 3; }

# Do not silently lose a symlink/device from user project data. The production
# restore path intentionally refuses links, so backup must fail loudly if such an
# unsupported member exists rather than producing a green but incomplete copy.
python3 - "$DATA_ROOT" "$TMP_DEST/files.tar" <<'PY'
from __future__ import annotations
import os, stat, sys, tarfile
from pathlib import Path

root = Path(sys.argv[1]).resolve()
out = Path(sys.argv[2])
for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
    base = Path(dirpath)
    for name in [*dirnames, *filenames]:
        path = base / name
        mode = path.lstat().st_mode
        if stat.S_ISLNK(mode) or not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
            raise SystemExit(f"unsupported data member for safe backup: {path.relative_to(root)}")
with tarfile.open(out, "w") as archive:
    archive.add(root, arcname="data", recursive=True)
with out.open("rb") as handle:
    os.fsync(handle.fileno())
PY
[ -s "$TMP_DEST/files.tar" ] || { echo "application data archive is empty" >&2; exit 3; }

[ -f .env ] && cp .env "$TMP_DEST/env.backup"
git rev-parse HEAD > "$TMP_DEST/git-head.txt" 2>/dev/null || true
{
  printf 'created_at=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  printf 'format=x1-backup-v4\n'
  printf 'data_root=%s\n' "$DATA_ROOT"
  printf 'writers_quiesced=%s\n' "$WRITERS_QUIESCED"
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
if [ "$WRITERS_QUIESCED" -eq 1 ]; then
  resume_writers
fi
trap - EXIT INT TERM
printf '%s\n' "$FINAL_DEST"
