#!/usr/bin/env bash
set -euo pipefail
umask 077

ROOT="${X1_INSTALL_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
cd "$ROOT"

info() { printf '[X1 update] %s\n' "$*"; }
fail() { printf '[X1 update] ERROR: %s\n' "$*" >&2; exit 1; }

if [ "$(id -u)" -ne 0 ]; then
  command -v sudo >/dev/null 2>&1 || fail "run as root or install sudo"
  exec sudo -E env X1_INSTALL_DIR="$ROOT" bash "$0" "$@"
fi
command -v git >/dev/null 2>&1 || fail "git is required"
command -v docker >/dev/null 2>&1 || fail "docker is required"
docker compose version >/dev/null 2>&1 || fail "Docker Compose v2 is required"

exec 9>"$ROOT/.x1-update.lock"
if command -v flock >/dev/null 2>&1; then
  flock -n 9 || fail "another X1 update is already running"
fi

[ -d .git ] || fail "$ROOT is not a Git repository"
[ -z "$(git status --porcelain --untracked-files=no)" ] || fail "tracked working tree has local changes; refusing automatic update"
OLD_HEAD="$(git rev-parse HEAD)"
RUNNING_SERVICES="$(docker compose ps --services --filter status=running 2>/dev/null || true)"

info "Fetching main without changing the working tree"
git fetch origin main
TARGET_HEAD="$(git rev-parse origin/main)"
if [ "$OLD_HEAD" = "$TARGET_HEAD" ]; then
  info "Already at latest main; running installer repair/verification"
  exec bash scripts/install.sh "$@"
fi

git merge-base --is-ancestor "$OLD_HEAD" "$TARGET_HEAD" || fail "origin/main is not a fast-forward from the installed revision"

# Backup, restore-drill, restore and legacy migration are a single format-aware
# toolset. Cache every helper from target HEAD before modifying the working tree,
# so even a very old installation can safely create and validate today's backup.
BACKUP_COPY="$(mktemp -t x1-backup.XXXXXX.sh)"
DRILL_COPY="$(mktemp -t x1-restore-drill.XXXXXX.sh)"
RESTORE_COPY="$(mktemp -t x1-restore.XXXXXX.sh)"
MIGRATE_COPY="$(mktemp -t x1-migrate-legacy.XXXXXX.sh)"
git show origin/main:scripts/backup.sh > "$BACKUP_COPY"
git show origin/main:scripts/restore_drill.sh > "$DRILL_COPY"
git show origin/main:scripts/restore.sh > "$RESTORE_COPY"
git show origin/main:scripts/migrate_legacy_data.sh > "$MIGRATE_COPY"
chmod 700 "$BACKUP_COPY" "$DRILL_COPY" "$RESTORE_COPY" "$MIGRATE_COPY"
BACKUP_PATH=""

cleanup_helpers() {
  rm -f "$BACKUP_COPY" "$DRILL_COPY" "$RESTORE_COPY" "$MIGRATE_COPY"
}

rollback() {
  local reason="$1"
  trap - ERR INT TERM
  set +e
  printf '[X1 update] ROLLBACK: %s\n' "$reason" >&2
  docker compose stop app image-worker sandbox-worker >/dev/null 2>&1 || true
  git reset --hard "$OLD_HEAD" >/dev/null 2>&1 || true
  if [ -n "$BACKUP_PATH" ] && [ -d "$BACKUP_PATH" ]; then
    X1_INSTALL_DIR="$ROOT" bash "$RESTORE_COPY" "$BACKUP_PATH" >/dev/null 2>&1 || printf '[X1 update] WARNING: automatic data restore failed; backup remains at %s\n' "$BACKUP_PATH" >&2
  fi
  docker compose build app sandbox-worker >/dev/null 2>&1 || true
  docker compose --profile inference up -d db searxng sandbox-worker llama app >/dev/null 2>&1 || true
  if printf '%s\n' "$RUNNING_SERVICES" | grep -qx image-worker; then
    docker compose --profile images up -d image-worker >/dev/null 2>&1 || true
  fi
  cleanup_helpers
  printf '[X1 update] Previous revision restored: %s\n' "$OLD_HEAD" >&2
  exit 2
}
trap 'rollback "update command failed at line $LINENO"' ERR
trap 'rollback "update interrupted"' INT TERM

info "Quiescing write-producing services"
docker compose stop app image-worker sandbox-worker >/dev/null 2>&1 || true

# A pre-Sprint39 installation may still keep all project/user files in a named
# *_x1_data volume. Copy it to the new host bind before the first v3 backup.
# The migration is copy-only and deliberately preserves the source volume, so a
# rollback to the old code still sees its original filesystem unchanged.
info "Checking for legacy named-volume user data"
X1_INSTALL_DIR="$ROOT" bash "$MIGRATE_COPY"

info "Creating consistent pre-update backup with target-version helper"
BACKUP_PATH="$(X1_INSTALL_DIR="$ROOT" X1_HOST_DATA_ROOT="${X1_HOST_DATA_ROOT:-$ROOT/data}" bash "$BACKUP_COPY")"
[ -d "$BACKUP_PATH" ] || rollback "backup path was not created"
info "Validating pre-update backup by target-version non-destructive restore drill"
X1_INSTALL_DIR="$ROOT" bash "$DRILL_COPY" "$BACKUP_PATH" >/dev/null

info "Fast-forwarding code to $TARGET_HEAD"
git merge --ff-only origin/main

info "Installing/migrating/verifying the new revision"
bash scripts/install.sh "$@"

if printf '%s\n' "$RUNNING_SERVICES" | grep -qx image-worker; then
  docker compose --profile images up -d image-worker
fi

trap - ERR INT TERM
cleanup_helpers
info "Update committed successfully: $OLD_HEAD -> $(git rev-parse HEAD)"
info "Rollback snapshot retained at: $BACKUP_PATH"
