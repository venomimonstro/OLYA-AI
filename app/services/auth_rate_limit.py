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


def _ip(value: str):
    try:
        return ipaddress.ip_address(value.strip())
    except ValueError:
        return None


def _trusted_proxy(address) -> bool:
    # X1's Compose publishes the app only on the host loopback address. Traffic
    # reaching it from RFC1918/loopback space is therefore the local reverse
    # proxy / Docker bridge, not an arbitrary Internet client. Global auth
    # limits remain active even if an internal component is compromised.
    return bool(address and (address.is_loopback or address.is_private))


def _client_ip(request: Request) -> str:
    peer_raw = request.client.host if request.client else "unknown"
    peer = _ip(peer_raw)
    if peer is None:
        return peer_raw[:128]
    if not _trusted_proxy(peer):
        return str(peer)

    forwarded = request.headers.get("x-forwarded-for", "")
    if not forwarded:
        return str(peer)
    chain = [_ip(raw) for raw in forwarded.split(",")]
    chain = [address for address in chain if address is not None]
    if not chain:
        return str(peer)
    # Walk from the proxy side toward the client. Trusted hops are skipped; the
    # first untrusted address is the effective client. This prevents a client
    # from winning by prepending an arbitrary X-Forwarded-For value.
    for address in reversed(chain):
        if not _trusted_proxy(address):
            return str(address)
    return str(chain[0])


def _reject_auth_load() -> None:
    raise HTTPException(
        status_code=429,
        detail="Authentication capacity is temporarily busy; retry shortly",
        headers={"Retry-After": "60"},
    )


def enforce_auth_rate_limit(request: Request, *, email: str, action: str, environment: str) -> None:
    if environment.lower() not in {"production", "prod", "stable"}:
        return
    global_limit = 120 if action == "register" else 300
    if not _limiter.consume(f"auth:{action}:global", limit=global_limit):
        _reject_auth_load()

    ip = _client_ip(request)
    ip_limit = 12 if action == "register" else 30
    if not _limiter.consume(f"auth:{action}:ip:{ip}", limit=ip_limit):
        _reject_auth_load()

    normalized_email = str(email or "").strip().casefold()
    if normalized_email:
        import hashlib
        email_key = hashlib.sha256(normalized_email.encode("utf-8", errors="ignore")).hexdigest()[:24]
        if not _limiter.consume(f"auth:{action}:email:{email_key}", limit=8):
            _reject_auth_load()
