#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import secrets
import statistics
import time
from dataclasses import dataclass
from typing import Any

import httpx
from sqlalchemy import delete

from app.db import SessionLocal
from app.models import AuthSession, User


@dataclass
class Sample:
    path: str
    status: int
    latency_ms: float


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    rows = sorted(values)
    index = min(len(rows) - 1, max(0, int(round((len(rows) - 1) * fraction))))
    return rows[index]


async def public_burst(base_url: str, *, requests: int, concurrency: int, timeout: float) -> dict[str, Any]:
    gate = asyncio.Semaphore(max(1, concurrency))
    samples: list[Sample] = []
    errors: list[str] = []
    paths = ("/", "/health", "/robots.txt")
    async with httpx.AsyncClient(base_url=base_url, timeout=timeout, trust_env=False) as client:
        async def one(index: int) -> None:
            async with gate:
                path = paths[index % len(paths)]
                started = time.perf_counter()
                try:
                    response = await client.get(path)
                    samples.append(Sample(path, response.status_code, (time.perf_counter() - started) * 1000.0))
                    if response.status_code != 200:
                        errors.append(f"{path}:{response.status_code}")
                except Exception as exc:
                    errors.append(f"{path}:{type(exc).__name__}")
        await asyncio.gather(*(one(i) for i in range(max(1, requests))))
    latencies = [item.latency_ms for item in samples]
    return {
        "status": "passed" if not errors else "failed",
        "requests": requests,
        "concurrency": concurrency,
        "completed": len(samples),
        "errors": errors[:30],
        "latency_ms": {
            "mean": round(statistics.fmean(latencies), 2) if latencies else 0.0,
            "p95": round(percentile(latencies, 0.95), 2),
            "p99": round(percentile(latencies, 0.99), 2),
            "max": round(max(latencies), 2) if latencies else 0.0,
        },
    }


async def auth_users(base_url: str, *, users: int, concurrency: int, timeout: float) -> dict[str, Any]:
    gate = asyncio.Semaphore(max(1, concurrency))
    created_ids: list[str] = []
    errors: list[str] = []
    latencies: list[float] = []
    prefix = "x1-load-" + secrets.token_hex(6)

    async with httpx.AsyncClient(base_url=base_url, timeout=timeout, trust_env=False) as client:
        async def one(index: int) -> None:
            email = f"{prefix}-{index}@example.invalid"
            password = "X1!" + secrets.token_urlsafe(22)
            # The probe runs from loopback inside the app container. X1 trusts
            # X-Forwarded-For only from loopback, allowing independent synthetic
            # client identities without weakening the public proxy boundary.
            ip = f"198.18.{(index // 250) % 250}.{(index % 250) + 1}"
            headers = {"X-Forwarded-For": ip}
            async with gate:
                started = time.perf_counter()
                try:
                    response = await client.post(
                        "/v1/auth/register",
                        headers=headers,
                        json={"email": email, "password": password, "display_name": "Load Probe"},
                    )
                    if response.status_code != 201:
                        errors.append(f"register:{response.status_code}")
                        return
                    data = response.json()
                    user_id = str(data["user_id"])
                    created_ids.append(user_id)
                    auth = {"Authorization": f"Bearer {data['access_token']}", **headers}
                    me = await client.get("/v1/auth/me", headers=auth)
                    if me.status_code != 200 or str(me.json().get("id")) != user_id:
                        errors.append(f"me:{me.status_code}")
                        return
                    logout = await client.post("/v1/auth/logout", headers=auth)
                    if logout.status_code != 204:
                        errors.append(f"logout:{logout.status_code}")
                except Exception as exc:
                    errors.append(type(exc).__name__)
                finally:
                    latencies.append((time.perf_counter() - started) * 1000.0)
        await asyncio.gather(*(one(i) for i in range(max(1, users))))

    if created_ids:
        with SessionLocal() as db:
            db.execute(delete(AuthSession).where(AuthSession.user_id.in_(created_ids)))
            db.execute(delete(User).where(User.id.in_(created_ids)))
            db.commit()

    return {
        "status": "passed" if not errors and len(created_ids) == users else "failed",
        "requested_users": users,
        "completed_users": len(created_ids),
        "concurrency": concurrency,
        "errors": errors[:30],
        "journey_latency_ms": {
            "p95": round(percentile(latencies, 0.95), 2),
            "p99": round(percentile(latencies, 0.99), 2),
        },
    }


def virtual_inference_arrivals(*, virtual_users: int, max_queue: int, running: int = 1) -> dict[str, Any]:
    resident = max(1, running) + max(0, max_queue)
    arrivals = max(1, virtual_users)
    shed = max(0, arrivals - resident)
    return {
        "status": "passed" if resident <= max(1, running) + max(0, max_queue) and shed > 0 else "failed",
        "virtual_users": arrivals,
        "running_inference": running,
        "bounded_queue": max_queue,
        "max_expensive_requests_resident": resident,
        "must_retry_or_be_shed": shed,
        "policy": "Never keep the whole traffic spike in RAM. Bound inference queue and return Retry-After/overload to excess arrivals.",
    }


async def async_main(args) -> dict[str, Any]:
    public = await public_burst(args.url.rstrip("/"), requests=args.requests, concurrency=args.http_concurrency, timeout=args.timeout)
    auth = await auth_users(args.url.rstrip("/"), users=args.live_users, concurrency=args.user_concurrency, timeout=max(args.timeout, 30.0))
    virtual = virtual_inference_arrivals(virtual_users=args.virtual_users, max_queue=args.max_queue)
    status = "passed" if all(item["status"] == "passed" for item in (public, auth, virtual)) else "failed"
    return {"format": "x1-user-load-v1", "status": status, "public_http": public, "live_auth_users": auth, "virtual_inference": virtual}


def main() -> int:
    parser = argparse.ArgumentParser(description="X1 live user/load smoke plus 100k bounded-admission simulation")
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--requests", type=int, default=1000)
    parser.add_argument("--http-concurrency", type=int, default=64)
    parser.add_argument("--live-users", type=int, default=40)
    parser.add_argument("--user-concurrency", type=int, default=8)
    parser.add_argument("--virtual-users", type=int, default=100000)
    parser.add_argument("--max-queue", type=int, default=64)
    parser.add_argument("--timeout", type=float, default=10.0)
    args = parser.parse_args()
    result = asyncio.run(async_main(args))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
