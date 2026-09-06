from __future__ import annotations

import threading
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request


class _AttemptLimiter:
    def __init__(self) -> None:
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def consume(self, key: str, *, limit: int, window_seconds: int = 60) -> bool:
        now = time.monotonic()
        cutoff = now - window_seconds
        with self._lock:
            bucket = self._events[key]
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()
            if len(bucket) >= limit:
                return False
            bucket.append(now)
            # Opportunistic cleanup keeps the in-memory map bounded on a long-
            # running single-node deployment without a background sweeper.
            if len(self._events) > 20_000:
                stale = [name for name, values in self._events.items() if not values or values[-1] <= cutoff]
                for name in stale[:5_000]:
                    self._events.pop(name, None)
            return True


_limiter = _AttemptLimiter()


def _client_ip(request: Request) -> str:
    peer = request.client.host if request.client else "unknown"
    # The default Compose topology binds X1 to loopback behind a local reverse
    # proxy. Only that trusted direct peer may supply the original client IP.
    if peer in {"127.0.0.1", "::1", "localhost"}:
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            candidate = forwarded.split(",", 1)[0].strip()
            if candidate:
                return candidate[:128]
    return peer[:128]


def enforce_auth_rate_limit(request: Request, *, email: str, action: str, environment: str) -> None:
    if environment.lower() not in {"production", "prod", "stable"}:
        return
    ip = _client_ip(request)
    ip_limit = 12 if action == "register" else 30
    if not _limiter.consume(f"auth:{action}:ip:{ip}", limit=ip_limit):
        raise HTTPException(status_code=429, detail="Too many authentication attempts", headers={"Retry-After": "60"})
    if not _limiter.consume(f"auth:{action}:email:{email.casefold()}", limit=8):
        raise HTTPException(status_code=429, detail="Too many authentication attempts", headers={"Retry-After": "60"})
