#!/usr/bin/env bash
set -euo pipefail

ROOT="${X1_INSTALL_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
TARGET="$ROOT/data"
mkdir -p "$TARGET"

# Sprint 39 moved X1 user artifacts from a Compose named volume to a host bind
# because the isolated sandbox worker must provide the host Docker daemon with
# real mount paths. A previous installation may still have all user files in an
# orphaned *_x1_data volume. Copy it exactly once when the new target is empty.
if find "$TARGET" -mindepth 1 -maxdepth 1 -print -quit | grep -q .; then
  echo "[X1] Host data directory already contains files; legacy volume migration skipped"
  exit 0
fi

mapfile -t candidates < <(docker volume ls --format '{{.Name}}' | grep -E '(^|_)x1_data$' || true)
if [ "${#candidates[@]}" -eq 0 ]; then
  echo "[X1] No legacy x1_data named volume found"
  exit 0
fi

for volume in "${candidates[@]}"; do
  count=$(docker run --rm -v "$volume:/from:ro" alpine:3.21 sh -c "find /from -mindepth 1 -maxdepth 1 | head -n 1 | wc -l" | tr -d '[:space:]')
  [ "${count:-0}" = "0" ] && continue
  echo "[X1] Migrating legacy user data from Docker volume: $volume"
  docker run --rm -v "$volume:/from:ro" -v "$TARGET:/to" alpine:3.21 sh -ceu 'cp -a /from/. /to/'
  chown -R 10001:10001 "$TARGET"
  echo "[X1] Legacy data copied. Original Docker volume is intentionally preserved for rollback: $volume"
  exit 0
done

echo "[X1] Legacy x1_data volumes were empty; nothing to migrate"
