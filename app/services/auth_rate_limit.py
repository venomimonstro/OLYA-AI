from __future__ import annotations

import ipaddress
import threading
import time
from collections import OrderedDict, deque

from fastapi import HTTPException, Request


class _AttemptLimiter:
    def __init__(self, *, max_buckets: int = 50_000) -> None:
        self._events: OrderedDict[str, deque[float]] = OrderedDict()
        self._lock = threading.Lock()
        self._max_buckets = max(1_000, int(max_buckets))

    def consume(self, key: str, *, limit: int, window_seconds: int = 60) -> bool:
        now = time.monotonic()
        cutoff = now - max(1, int(window_seconds))
        with self._lock:
            bucket = self._events.get(key)
            if bucket is None:
                # Sweep oldest stale buckets first. If an attacker keeps spraying
                # never-before-seen emails/IPs, evict the oldest bucket instead of
                # letting the protection structure itself become an unbounded DoS.
                while self._events:
                    oldest_key, oldest_values = next(iter(self._events.items()))
                    while oldest_values and oldest_values[0] <= cutoff:
                        oldest_values.popleft()
                    if oldest_values and len(self._events) < self._max_buckets:
                        break
                    self._events.pop(oldest_key, None)
                    if len(self._events) < self._max_buckets:
                        break
                if len(self._events) >= self._max_buckets:
                    self._events.popitem(last=False)
                bucket = deque()
                self._events[key] = bucket
            else:
                self._events.move_to_end(key)

            while bucket and bucket[0] <= cutoff:
                bucket.popleft()
            if len(bucket) >= max(1, int(limit)):
                return False
            bucket.append(now)
            return True


_limiter = _AttemptLimiter()


def _valid_ip(value: str) -> str | None:
    candidate = value.strip()
    try:
        return str(ipaddress.ip_address(candidate))
    except ValueError:
        return None


def _client_ip(request: Request) -> str:
    peer_raw = request.client.host if request.client else "unknown"
    peer = _valid_ip(peer_raw) or peer_raw[:128]
    # Only a direct loopback reverse proxy may assert the original address.
    if peer in {"127.0.0.1", "::1"}:
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            candidate = _valid_ip(forwarded.split(",", 1)[0])
            if candidate:
                return candidate
    return peer


def enforce_auth_rate_limit(request: Request, *, email: str, action: str, environment: str) -> None:
    if environment.lower() not in {"production", "prod", "stable"}:
        return
    ip = _client_ip(request)
    ip_limit = 12 if action == "register" else 30
    if not _limiter.consume(f"auth:{action}:ip:{ip}", limit=ip_limit):
        raise HTTPException(status_code=429, detail="Too many authentication attempts", headers={"Retry-After": "60"})
    # Hashing prevents attacker-controlled email strings from becoming large map
    # keys while preserving independent per-account throttling.
    import hashlib
    email_key = hashlib.sha256(email.casefold().encode("utf-8", errors="ignore")).hexdigest()[:24]
    if not _limiter.consume(f"auth:{action}:email:{email_key}", limit=8):
        raise HTTPException(status_code=429, detail="Too many authentication attempts", headers={"Retry-After": "60"})
