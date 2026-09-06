#!/usr/bin/env bash
set -euo pipefail

REPO_URL="${X1_REPO_URL:-https://github.com/venomimonstro/OLYA-AI.git}"
INSTALL_DIR="${X1_INSTALL_DIR:-/opt/olya-ai}"

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

if ! command -v git >/dev/null 2>&1; then
  if command -v apt-get >/dev/null 2>&1; then
    apt-get update
    DEBIAN_FRONTEND=noninteractive apt-get install -y git ca-certificates python3
  else
    echo "[X1] ERROR: git is required and automatic package installation supports Debian/Ubuntu" >&2
    exit 1
  fi
fi

if [ -d "$INSTALL_DIR/.git" ]; then
  echo "[X1] Existing installation found at $INSTALL_DIR; fast-forwarding main"
  git -C "$INSTALL_DIR" fetch origin main
  git -C "$INSTALL_DIR" checkout main
  git -C "$INSTALL_DIR" merge --ff-only origin/main
elif [ -e "$INSTALL_DIR" ]; then
  echo "[X1] ERROR: $INSTALL_DIR exists but is not an X1 Git repository" >&2
  exit 1
else
  mkdir -p "$(dirname "$INSTALL_DIR")"
  git clone --depth 1 --branch main "$REPO_URL" "$INSTALL_DIR"
fi

exec bash "$INSTALL_DIR/scripts/install.sh" "$@"
