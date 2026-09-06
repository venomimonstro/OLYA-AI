#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time

import httpx


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    rows = sorted(values)
    index = min(len(rows) - 1, max(0, int(round((len(rows) - 1) * fraction))))
    return rows[index]


async def run_probe(base_url: str, requests: int, concurrency: int, timeout: float) -> dict:
    semaphore = asyncio.Semaphore(max(1, concurrency))
    latencies: list[float] = []
    failures: list[dict] = []
    statuses: dict[str, int] = {}

    async with httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=timeout, trust_env=False) as client:
        async def one(index: int) -> None:
            path = "/health" if index % 3 else "/ready"
            async with semaphore:
                started = time.perf_counter()
                try:
                    response = await client.get(path)
                    elapsed = (time.perf_counter() - started) * 1000.0
                    latencies.append(elapsed)
                    key = f"{path}:{response.status_code}"
                    statuses[key] = statuses.get(key, 0) + 1
                    if path == "/health" and response.status_code != 200:
                        failures.append({"path": path, "status": response.status_code})
                    elif path == "/ready" and response.status_code not in {200, 503}:
                        failures.append({"path": path, "status": response.status_code})
                except Exception as exc:
                    latencies.append((time.perf_counter() - started) * 1000.0)
                    failures.append({"path": path, "error": type(exc).__name__})

        await asyncio.gather(*(one(index) for index in range(max(1, requests))))

    p95 = percentile(latencies, 0.95)
    p99 = percentile(latencies, 0.99)
    result = {
        "status": "passed" if not failures else "failed",
        "requests": max(1, requests),
        "concurrency": max(1, concurrency),
        "failures": failures[:20],
        "failure_count": len(failures),
        "status_counts": statuses,
        "latency_ms": {
            "mean": round(statistics.fmean(latencies), 2) if latencies else 0.0,
            "p95": round(p95, 2),
            "p99": round(p99, 2),
            "max": round(max(latencies), 2) if latencies else 0.0,
        },
    }
    # Health/readiness checks are intentionally cheap. Very slow probes are a
    # release warning even if all requests eventually succeeded.
    if not failures and p95 > 2500:
        result["status"] = "degraded"
        result["warning"] = "p95 health/readiness latency exceeds 2500 ms"
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Bounded X1 production HTTP smoke load")
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--requests", type=int, default=60)
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--timeout", type=float, default=8.0)
    args = parser.parse_args()
    result = asyncio.run(run_probe(args.url, args.requests, args.concurrency, args.timeout))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "passed" else 1 if result["status"] == "degraded" else 2


if __name__ == "__main__":
    raise SystemExit(main())
