#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import time
from dataclasses import dataclass

import httpx


@dataclass(frozen=True)
class Sample:
    user: int
    round: int
    status: int
    latency_ms: int
    queue_waiting: int
    overload_lane: str
    ok: bool
    error: str = ""


def _percentile(values: list[int], p: float) -> int:
    if not values:
        return 0
    rows = sorted(values)
    index = max(0, min(len(rows) - 1, int((len(rows) - 1) * p)))
    return int(rows[index])


async def _one(client: httpx.AsyncClient, base_url: str, token: str, user_index: int, round_index: int, timeout: float) -> Sample:
    started = time.perf_counter()
    try:
        response = await client.post(
            base_url.rstrip("/") + "/v1/chat",
            headers={"Authorization": "Bearer " + token, "X-X1-Deadline-Ms": str(int(timeout * 1000))},
            json={"message": f"Load acceptance user {user_index} round {round_index}: reply with OK and one short sentence.", "mode": "fast", "verification": "off", "web": "off"},
            timeout=timeout + 5,
        )
        latency = int((time.perf_counter() - started) * 1000)
        queue = int(response.headers.get("X-X1-Queue-Waiting") or 0)
        lane = response.headers.get("X-X1-Overload-Lane") or ""
        ok = response.status_code == 200
        return Sample(user_index, round_index, response.status_code, latency, queue, lane, ok, "" if ok else response.text[:300])
    except Exception as exc:
        return Sample(user_index, round_index, 0, int((time.perf_counter() - started) * 1000), 0, "", False, type(exc).__name__)


async def run(base_url: str, tokens: list[str], *, rounds: int, timeout: float, p95_limit_ms: int, max_error_rate: float) -> dict:
    if len(tokens) < 10:
        raise RuntimeError("Real load acceptance requires at least 10 distinct user tokens")
    if len(set(tokens)) != len(tokens):
        raise RuntimeError("Load acceptance tokens must represent distinct sessions/users")
    limits = httpx.Limits(max_connections=max(20, len(tokens) * 2), max_keepalive_connections=max(10, len(tokens)))
    async with httpx.AsyncClient(trust_env=False, limits=limits) as client:
        started = time.perf_counter()
        samples: list[Sample] = []
        for round_index in range(1, rounds + 1):
            batch = await asyncio.gather(*[
                _one(client, base_url, token, index + 1, round_index, timeout)
                for index, token in enumerate(tokens)
            ])
            samples.extend(batch)
        elapsed = max(0.001, time.perf_counter() - started)

    latencies = [row.latency_ms for row in samples]
    successes = [row for row in samples if row.ok]
    failures = [row for row in samples if not row.ok]
    error_rate = len(failures) / max(1, len(samples))
    p95 = _percentile(latencies, .95)
    gates = {
        "users_at_least_10": len(tokens) >= 10,
        "all_users_exercised": len({row.user for row in samples}) == len(tokens),
        "error_rate": error_rate <= max_error_rate,
        "p95_latency_ms": p95 <= p95_limit_ms,
        "successes_present": bool(successes),
    }
    return {
        "format": "x1-real-load-acceptance-v1",
        "target": base_url,
        "virtual_users": len(tokens),
        "rounds": rounds,
        "requests": len(samples),
        "successes": len(successes),
        "failures": len(failures),
        "error_rate": round(error_rate, 4),
        "throughput_requests_per_second": round(len(samples) / elapsed, 3),
        "latency_ms": {
            "median": int(statistics.median(latencies)) if latencies else 0,
            "p95": p95,
            "max": max(latencies, default=0),
        },
        "queue": {
            "max_waiting_observed": max((row.queue_waiting for row in samples), default=0),
            "overload_responses": sum(1 for row in samples if row.status == 503),
        },
        "gates": gates,
        "passed": all(gates.values()),
        "failure_samples": [row.__dict__ for row in failures[:20]],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run X1 real load acceptance with >=10 distinct authenticated users")
    parser.add_argument("--base-url", default=os.environ.get("X1_LOAD_BASE_URL", "http://127.0.0.1:8000"))
    parser.add_argument("--rounds", type=int, default=2)
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--p95-limit-ms", type=int, default=120_000)
    parser.add_argument("--max-error-rate", type=float, default=0.05)
    args = parser.parse_args()
    tokens = [value.strip() for value in os.environ.get("X1_LOAD_TOKENS", "").split(",") if value.strip()]
    try:
        result = asyncio.run(run(args.base_url, tokens, rounds=max(1, min(args.rounds, 20)), timeout=max(5.0, args.timeout), p95_limit_ms=max(1000, args.p95_limit_ms), max_error_rate=max(0.0, min(args.max_error_rate, .5))))
    except RuntimeError as exc:
        print(json.dumps({"format": "x1-real-load-acceptance-v1", "passed": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
