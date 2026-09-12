#!/usr/bin/env bash
set -euo pipefail

REPO_URL="${X1_REPO_URL:-https://github.com/venomimonstro/OLYA-AI.git}"
INSTALL_DIR="${X1_INSTALL_DIR:-/opt/olya-ai}"
REQUESTED_PROFILE="${X1_INSTALL_PROFILE:-auto}"

if [ "$(uname -s)" != "Linux" ]; then
  echo "[X1] ERROR: Linux is required" >&2
  exit 1
fi

if [ "$(id -u)" -ne 0 ]; then
  if command -v sudo >/dev/null 2>&1; then
    exec sudo -E bash "$0" "$@"
  fi
  echo "[X1] ERROR: run as root or install sudo" >&2
  exit 1
fi

if ! command -v git >/dev/null 2>&1 || ! command -v python3 >/dev/null 2>&1; then
  if command -v apt-get >/dev/null 2>&1; then
    apt-get update
    DEBIAN_FRONTEND=noninteractive apt-get install -y git ca-certificates python3 curl
  else
    echo "[X1] ERROR: git/python3 are required and automatic package installation supports Debian/Ubuntu" >&2
    exit 1
  fi
fi

profile_from_host() {
  if [ "$REQUESTED_PROFILE" != "auto" ]; then
    printf '%s' "$REQUESTED_PROFILE"
    return
  fi
  if [ -f "$INSTALL_DIR/.env" ] && grep -q '^X1_SERVER_OPTIMIZATION_PROFILE=starter_6gb$' "$INSTALL_DIR/.env"; then
    printf 'starter_6gb'
    return
  fi
  mem_kb=$(awk '/MemTotal/ {print $2}' /proc/meminfo 2>/dev/null || printf '0')
  if [ "${mem_kb:-0}" -gt 0 ] && [ "$mem_kb" -lt $((8*1024*1024)) ]; then
    printf 'starter_6gb'
  else
    printf 'full'
  fi
}

PROFILE="$(profile_from_host)"
case "$PROFILE" in starter_6gb|full) ;; *) echo "[X1] ERROR: X1_INSTALL_PROFILE must be auto, starter_6gb or full" >&2; exit 1;; esac

echo "[X1] Selected installation profile: $PROFILE"

if [ -d "$INSTALL_DIR/.git" ]; then
  echo "[X1] Existing installation found at $INSTALL_DIR; using transactional updater"
  git -C "$INSTALL_DIR" fetch origin main
  UPDATE_TMP="$(mktemp -t x1-update.XXXXXX.sh)"
  trap 'rm -f "$UPDATE_TMP"' EXIT
  git -C "$INSTALL_DIR" show origin/main:scripts/update.sh > "$UPDATE_TMP"
  chmod 700 "$UPDATE_TMP"
  X1_INSTALL_DIR="$INSTALL_DIR" X1_INSTALL_PROFILE="$PROFILE" bash "$UPDATE_TMP" "$@"
  status=$?
  rm -f "$UPDATE_TMP"
  trap - EXIT
  exit "$status"
elif [ -e "$INSTALL_DIR" ]; then
  echo "[X1] ERROR: $INSTALL_DIR exists but is not an X1 Git repository" >&2
  exit 1
else
  mkdir -p "$(dirname "$INSTALL_DIR")"
  git clone --depth 1 --branch main "$REPO_URL" "$INSTALL_DIR"
fi

if [ "$PROFILE" = "starter_6gb" ]; then
  exec bash "$INSTALL_DIR/scripts/install_starter_6gb.sh" "$@"
fi
exec bash "$INSTALL_DIR/scripts/install.sh" "$@"
