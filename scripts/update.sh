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

# Preserve rollback code outside the repository because git reset may move to a
# revision that predates Sprint 40. Older installations do not yet have
# scripts/restore.sh, so obtain the exact target copy without checking target code
# out into the working tree.
RESTORE_COPY="$(mktemp -t x1-restore.XXXXXX.sh)"
if [ -f scripts/restore.sh ]; then
  cp scripts/restore.sh "$RESTORE_COPY"
else
  git show origin/main:scripts/restore.sh > "$RESTORE_COPY"
fi
chmod 700 "$RESTORE_COPY"
BACKUP_PATH=""

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
  rm -f "$RESTORE_COPY"
  printf '[X1 update] Previous revision restored: %s\n' "$OLD_HEAD" >&2
  exit 2
}
trap 'rollback "update command failed at line $LINENO"' ERR
trap 'rollback "update interrupted"' INT TERM

info "Quiescing write-producing services"
docker compose stop app image-worker sandbox-worker >/dev/null 2>&1 || true

info "Creating consistent pre-update backup"
# When upgrading a pre-Sprint40 installation, its backup.sh still requires the
# app container. Use the target backup implementation without checking out target
# code so the quiesced app can stay stopped.
if grep -q 'authoritative /app/data' scripts/backup.sh 2>/dev/null; then
  BACKUP_HELPER="$(mktemp -t x1-backup.XXXXXX.sh)"
  git show origin/main:scripts/backup.sh > "$BACKUP_HELPER"
  chmod 700 "$BACKUP_HELPER"
  BACKUP_PATH="$(X1_HOST_DATA_ROOT="${X1_HOST_DATA_ROOT:-$ROOT/data}" bash "$BACKUP_HELPER")"
  rm -f "$BACKUP_HELPER"
else
  BACKUP_PATH="$(bash scripts/backup.sh)"
fi
[ -d "$BACKUP_PATH" ] || rollback "backup path was not created"
info "Validating pre-update backup by non-destructive restore drill"
bash scripts/restore_drill.sh "$BACKUP_PATH" >/dev/null

info "Fast-forwarding code to $TARGET_HEAD"
git merge --ff-only origin/main

info "Installing/migrating/verifying the new revision"
bash scripts/install.sh "$@"

if printf '%s\n' "$RUNNING_SERVICES" | grep -qx image-worker; then
  docker compose --profile images up -d image-worker
fi

trap - ERR INT TERM
rm -f "$RESTORE_COPY"
info "Update committed successfully: $OLD_HEAD -> $(git rev-parse HEAD)"
info "Rollback snapshot retained at: $BACKUP_PATH"
