#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import re
from pathlib import Path

from app.services.server_profiles import PROFILE_NAMES, REQUEST_FILE, persist_active_profile, profile_for_host, read_staged_profile

ROOT = Path(__file__).resolve().parents[1]


def host_ram_gib() -> float:
    text = Path('/proc/meminfo').read_text('utf-8')
    match = re.search(r'^MemTotal:\s+(\d+)\s+kB', text, re.MULTILINE)
    if not match:
        raise RuntimeError('Cannot detect host memory')
    return int(match.group(1)) / 1024.0 / 1024.0


def host_cores() -> int:
    return max(1, int(os.cpu_count() or 1))


def set_env_values(path: Path, values: dict[str, str]) -> None:
    lines = path.read_text('utf-8').splitlines() if path.exists() else []
    pending = dict(values)
    out: list[str] = []
    for line in lines:
        if line and not line.lstrip().startswith('#') and '=' in line:
            key = line.split('=', 1)[0]
            if key in pending:
                out.append(f'{key}={pending.pop(key)}')
                continue
        out.append(line)
    if pending:
        if out and out[-1].strip():
            out.append('')
        out.append('# Sprint 55 server optimization envelope')
        out.extend(f'{key}={value}' for key, value in sorted(pending.items()))
    tmp = path.with_suffix(path.suffix + '.profile.tmp')
    tmp.write_text('\n'.join(out).rstrip() + '\n', 'utf-8')
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def main() -> int:
    parser = argparse.ArgumentParser(description='Apply one safe X1 server optimization profile to .env')
    parser.add_argument('--profile', choices=PROFILE_NAMES, default='')
    parser.add_argument('--env-file', default=str(ROOT / '.env'))
    parser.add_argument('--data-root', default=str(ROOT / 'data'))
    parser.add_argument('--staged', action='store_true', help='Apply data/server-profile-request.json')
    parser.add_argument('--print-only', action='store_true')
    args = parser.parse_args()

    ram = host_ram_gib()
    cores = host_cores()
    profile = args.profile
    if args.staged:
        staged = read_staged_profile(args.data_root)
        if not staged or staged.get('status') != 'staged':
            raise SystemExit('No valid staged server profile request')
        profile = str((staged.get('envelope') or {}).get('profile') or '')
    if not profile:
        profile = 'optimal'

    envelope = profile_for_host(profile, ram, cores)
    values = envelope.env()
    if args.print_only:
        for key, value in sorted(values.items()):
            print(f'{key}={value}')
        return 0
    set_env_values(Path(args.env_file), values)
    persist_active_profile(args.data_root, profile, ram, cores)
    if args.staged:
        (Path(args.data_root).resolve() / REQUEST_FILE).unlink(missing_ok=True)
    print(
        f'[X1] server profile applied: {envelope.profile}; RAM={ram:.2f}GiB; '
        f'context={envelope.context_tokens}; llama={envelope.llama_memory_gib}GiB; '
        f'Qwen slots={envelope.max_concurrent_generations}'
    )
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
