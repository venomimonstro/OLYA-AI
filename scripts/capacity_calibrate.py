#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import os
import re
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
TIERS = (8192, 12288, 16384)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def meminfo() -> dict[str, int]:
    result: dict[str, int] = {}
    try:
        for line in Path('/proc/meminfo').read_text('utf-8').splitlines():
            key, value = line.split(':', 1)
            match = re.search(r'(\d+)', value)
            if match:
                result[key] = int(match.group(1)) * 1024
    except (OSError, ValueError):
        pass
    return result


def cpu_info() -> dict[str, Any]:
    model = ''
    flags: set[str] = set()
    try:
        for line in Path('/proc/cpuinfo').read_text('utf-8', errors='replace').splitlines():
            if not model and line.lower().startswith('model name'):
                model = line.split(':', 1)[1].strip()
            elif line.lower().startswith('flags'):
                flags.update(line.split(':', 1)[1].split())
    except OSError:
        pass
    return {'model': model or 'unknown', 'logical_cpus': os.cpu_count() or 1, 'avx2': 'avx2' in flags, 'avx512': any(flag.startswith('avx512') for flag in flags)}


def run(argv: list[str], timeout: int = 30) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, cwd=ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout, shell=False)


def llama_pid() -> int:
    try:
        cid = run(['docker', 'compose', 'ps', '-q', 'llama'], 20).stdout.strip()
        if not cid:
            return 0
        value = run(['docker', 'inspect', '-f', '{{.State.Pid}}', cid], 20).stdout.strip()
        return int(value or 0)
    except (OSError, ValueError, subprocess.SubprocessError):
        return 0


def process_memory(pid: int) -> tuple[int, int]:
    if pid <= 0:
        return 0, 0
    rss = swap = 0
    try:
        for line in Path(f'/proc/{pid}/status').read_text('utf-8').splitlines():
            if line.startswith('VmRSS:'):
                rss = int(line.split()[1]) * 1024
            elif line.startswith('VmSwap:'):
                swap = int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        pass
    return rss, swap


def _last_json(text: str) -> dict[str, Any] | None:
    start = text.find('{')
    if start < 0:
        return None
    try:
        value = json.loads(text[start:])
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def probe_once(tier: int, timeout: int, sample_interval: float) -> dict[str, Any]:
    pid = llama_pid()
    if pid <= 0:
        return {'status': 'failed', 'tier': tier, 'error': 'llama_container_not_running'}
    baseline_mem = meminfo()
    baseline_rss, baseline_swap = process_memory(pid)
    samples: list[dict[str, int]] = []
    stop = threading.Event()

    def sampler() -> None:
        while not stop.is_set():
            info = meminfo()
            rss, swap = process_memory(pid)
            samples.append({'rss_bytes': rss, 'swap_bytes': swap, 'mem_available_bytes': int(info.get('MemAvailable', 0))})
            stop.wait(max(0.1, sample_interval))

    thread = threading.Thread(target=sampler, daemon=True)
    thread.start()
    started = time.perf_counter()
    try:
        completed = run(['docker', 'compose', 'exec', '-T', 'app', 'python', '-m', 'scripts.long_context_probe', '--context-tokens', str(tier), '--output-tokens', '64', '--live-url', 'http://llama:8080', '--timeout', str(timeout)], timeout=timeout + 30)
    except subprocess.TimeoutExpired:
        completed = None
    finally:
        stop.set()
        thread.join(timeout=2)
    duration_ms = int((time.perf_counter() - started) * 1000)
    final_info = meminfo()
    final_rss, final_swap = process_memory(pid)
    if not samples:
        samples.append({'rss_bytes': final_rss, 'swap_bytes': final_swap, 'mem_available_bytes': int(final_info.get('MemAvailable', 0))})

    payload = _last_json(completed.stdout) if completed is not None else None
    live = (payload or {}).get('live') if isinstance((payload or {}).get('live'), dict) else {}
    passed = bool(completed is not None and completed.returncode == 0 and payload and payload.get('status') == 'passed' and live.get('status') == 'passed')
    peak_rss = max(item['rss_bytes'] for item in samples)
    peak_swap = max(item['swap_bytes'] for item in samples)
    min_available = min((item['mem_available_bytes'] for item in samples if item['mem_available_bytes'] > 0), default=0)
    return {'status': 'passed' if passed else 'failed', 'tier': tier, 'exit_code': None if completed is None else completed.returncode, 'duration_ms': duration_ms, 'model_latency_ms': int(live.get('latency_ms') or duration_ms), 'prompt_chars': int(live.get('prompt_chars') or 0), 'peak_llama_rss_bytes': peak_rss, 'baseline_llama_rss_bytes': baseline_rss, 'peak_llama_swap_bytes': peak_swap, 'baseline_llama_swap_bytes': baseline_swap, 'swap_growth_bytes': max(0, peak_swap - baseline_swap), 'min_host_mem_available_bytes': min_available, 'baseline_host_mem_available_bytes': int(baseline_mem.get('MemAvailable', 0)), 'stdout_tail': '' if completed is None else completed.stdout[-1000:], 'stderr_tail': 'timeout' if completed is None else completed.stderr[-1000:]}


def percentile(values: list[int], q: float) -> int:
    if not values:
        return 0
    values = sorted(values)
    index = max(0, min(len(values) - 1, math.ceil(q * len(values)) - 1))
    return int(values[index])


def summarize_tier(tier: int, runs: list[dict[str, Any]], *, min_headroom_bytes: int, max_swap_growth_bytes: int, max_latency_ms: int) -> dict[str, Any]:
    p95 = percentile([int(run.get('model_latency_ms') or 0) for run in runs], 0.95)
    peak_rss = max((int(run.get('peak_llama_rss_bytes') or 0) for run in runs), default=0)
    peak_swap_growth = max((int(run.get('swap_growth_bytes') or 0) for run in runs), default=0)
    min_available = min((int(run.get('min_host_mem_available_bytes') or 0) for run in runs if int(run.get('min_host_mem_available_bytes') or 0) > 0), default=0)
    reasons: list[str] = []
    if not runs or any(run.get('status') != 'passed' for run in runs): reasons.append('inference_probe_failed')
    if min_available and min_available < min_headroom_bytes: reasons.append('memory_headroom_low')
    if peak_swap_growth > max_swap_growth_bytes: reasons.append('swap_growth_high')
    if p95 > max_latency_ms: reasons.append('latency_above_guardrail')
    return {'tier': tier, 'status': 'passed' if not reasons else 'failed', 'reasons': reasons, 'samples': len(runs), 'p95_latency_ms': p95, 'peak_llama_rss_bytes': peak_rss, 'max_swap_growth_bytes': peak_swap_growth, 'min_host_mem_available_bytes': min_available, 'runs': runs}


def recommendation(candidates: list[dict[str, Any]], max_queue_wait_seconds: int) -> dict[str, Any]:
    passed = [item for item in candidates if item.get('status') == 'passed']
    if not passed:
        return {}
    selected = max(passed, key=lambda item: int(item['tier']))
    latency_seconds = max(1.0, float(selected.get('p95_latency_ms') or 1000) / 1000.0)
    queue_size = max(2, min(16, int(max_queue_wait_seconds // latency_seconds)))
    queue_timeout = max(30.0, min(180.0, round(latency_seconds * 2.0, 1)))
    return {'max_context_tokens': min(8192, int(selected['tier'])), 'deep_context_tokens': int(selected['tier']), 'max_concurrent_generations': 1, 'max_queue_size': queue_size, 'inference_queue_timeout_seconds': queue_timeout, 'selected_p95_latency_ms': int(selected.get('p95_latency_ms') or 0), 'selection_policy': 'largest_passing_context_with_memory_swap_latency_guardrails'}


def write_atomic(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(payload, 'utf-8')
    os.replace(tmp, path)


def main() -> int:
    parser = argparse.ArgumentParser(description='X1 target-node CPU/RAM capacity calibration')
    parser.add_argument('--tiers', default='8192,12288,16384')
    parser.add_argument('--samples', type=int, default=1)
    parser.add_argument('--timeout', type=int, default=300)
    parser.add_argument('--min-memory-headroom-mb', type=int, default=3072)
    parser.add_argument('--max-swap-growth-mb', type=int, default=256)
    parser.add_argument('--max-latency-ms', type=int, default=300000)
    parser.add_argument('--max-queue-wait-seconds', type=int, default=180)
    parser.add_argument('--sample-interval', type=float, default=0.5)
    parser.add_argument('--report', default='backups/capacity-latest.json')
    parser.add_argument('--emit-env', default='backups/capacity-recommended.env')
    args = parser.parse_args()

    requested: list[int] = []
    for item in args.tiers.split(','):
        value = int(item.strip())
        if value not in TIERS: raise SystemExit(f'Unsupported tier: {value}; supported: {TIERS}')
        if value not in requested: requested.append(value)
    samples = max(1, min(3, args.samples))
    started = utcnow()
    memory = meminfo()
    host = {'cpu': cpu_info(), 'memory_total_bytes': int(memory.get('MemTotal', 0)), 'swap_total_bytes': int(memory.get('SwapTotal', 0))}
    candidates: list[dict[str, Any]] = []
    for tier in requested:
        runs = [probe_once(tier, args.timeout, args.sample_interval) for _ in range(samples)]
        candidates.append(summarize_tier(tier, runs, min_headroom_bytes=max(0, args.min_memory_headroom_mb) * 1024 * 1024, max_swap_growth_bytes=max(0, args.max_swap_growth_mb) * 1024 * 1024, max_latency_ms=max(1000, args.max_latency_ms)))
    rec = recommendation(candidates, max(30, args.max_queue_wait_seconds))
    result = {'format': 'x1-capacity-v1', 'status': 'passed' if rec else 'failed', 'started_at': started.isoformat(), 'finished_at': utcnow().isoformat(), 'host': host, 'guardrails': {'min_memory_headroom_mb': args.min_memory_headroom_mb, 'max_swap_growth_mb': args.max_swap_growth_mb, 'max_latency_ms': args.max_latency_ms, 'max_queue_wait_seconds': args.max_queue_wait_seconds}, 'candidates': candidates, 'recommendation': rec}
    report = Path(args.report)
    if not report.is_absolute(): report = ROOT / report
    write_atomic(report, json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    if rec and args.emit_env:
        env_path = Path(args.emit_env)
        if not env_path.is_absolute(): env_path = ROOT / env_path
        env_text = '# Generated by X1 Sprint 36 capacity calibration. Review before applying.\n' + f'X1_MAX_CONTEXT_TOKENS={rec["max_context_tokens"]}\nX1_DEEP_CONTEXT_TOKENS={rec["deep_context_tokens"]}\nX1_MAX_CONCURRENT_GENERATIONS={rec["max_concurrent_generations"]}\nX1_MAX_QUEUE_SIZE={rec["max_queue_size"]}\nX1_INFERENCE_QUEUE_TIMEOUT_SECONDS={rec["inference_queue_timeout_seconds"]}\n'
        write_atomic(env_path, env_text)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['status'] == 'passed' else 2


if __name__ == '__main__':
    raise SystemExit(main())
