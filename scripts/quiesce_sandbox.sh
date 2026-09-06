#!/usr/bin/env bash
set -euo pipefail

# Stop only containers explicitly created and labelled by X1's sandbox worker.
# This is used after stopping sandbox-worker so no new execution/preview can
# appear while backup, restore or upgrade is switching application data.
command -v docker >/dev/null 2>&1 || exit 0

ids="$({
  docker ps -aq --filter 'label=x1.sandbox.preview=true' 2>/dev/null || true
  docker ps -aq --filter 'label=x1.sandbox.execution=true' 2>/dev/null || true
} | awk 'NF' | sort -u)"

[ -n "$ids" ] || exit 0
# shellcheck disable=SC2086
# IDs originate from docker itself, not user input. rm -f also removes detached
# previews and execution containers whose client process disappeared.
docker rm -f $ids >/dev/null
