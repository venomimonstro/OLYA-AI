from __future__ import annotations

import asyncio
import hashlib
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from dataclasses import dataclass
from time import monotonic


class OverloadRejected(RuntimeError):
    def __init__(self, message: str, *, retry_after: int = 5, reason: str = "capacity") -> None:
        super().__init__(message)
        self.retry_after = max(1, int(retry_after))
        self.reason = reason


@dataclass(frozen=True)
class LaneSnapshot:
    name: str
    active: int
    waiting: int
    max_concurrent: int
    max_queue: int
    breaker_state: str
    breaker_failures: int


class FairOverloadLane:
    """Pre-route admission with bounded FIFO queue, principal fairness and breaker."""

    def __init__(self, name: str, *, max_concurrent: int, max_queue: int, queue_timeout_seconds: float,
                 max_queued_per_principal: int = 2, breaker_failures: int = 5,
                 breaker_cooldown_seconds: float = 20.0) -> None:
        self.name = name
        self.max_concurrent = max(1, int(max_concurrent))
        self.max_queue = max(0, int(max_queue))
        self.queue_timeout_seconds = max(0.1, float(queue_timeout_seconds))
        self.max_queued_per_principal = max(1, int(max_queued_per_principal))
        self.breaker_threshold = max(2, int(breaker_failures))
        self.breaker_cooldown_seconds = max(1.0, float(breaker_cooldown_seconds))
        self._active = 0
        self._queue: deque[tuple[asyncio.Future[None], str]] = deque()
        self._queued_by_principal: dict[str, int] = defaultdict(int)
        self._lock = asyncio.Lock()
        self._consecutive_failures = 0
        self._opened_at = 0.0
        self._half_open_inflight = False

    def _breaker_state_unlocked(self, now: float | None = None) -> str:
        if not self._opened_at:
            return "closed"
        current = monotonic() if now is None else now
        return "half_open" if current - self._opened_at >= self.breaker_cooldown_seconds else "open"

    def snapshot(self) -> LaneSnapshot:
        return LaneSnapshot(self.name, self._active, len(self._queue), self.max_concurrent, self.max_queue,
                            self._breaker_state_unlocked(), self._consecutive_failures)

    async def _acquire(self, principal: str) -> None:
        loop = asyncio.get_running_loop(); waiter: asyncio.Future[None] | None = None
        async with self._lock:
            state = self._breaker_state_unlocked()
            if state == "open":
                remaining = self.breaker_cooldown_seconds - (monotonic() - self._opened_at)
                raise OverloadRejected(f"{self.name} circuit breaker is open", retry_after=max(1, int(remaining + .999)), reason="circuit_open")
            if state == "half_open":
                if self._half_open_inflight:
                    raise OverloadRejected(f"{self.name} is recovering", retry_after=2, reason="half_open")
                self._half_open_inflight = True; self._active += 1; return
            if self._active < self.max_concurrent and not self._queue:
                self._active += 1; return
            if len(self._queue) >= self.max_queue:
                raise OverloadRejected(f"{self.name} queue is full", retry_after=5, reason="queue_full")
            if self._queued_by_principal[principal] >= self.max_queued_per_principal:
                raise OverloadRejected(f"{self.name} per-user queue share is full", retry_after=5, reason="fairness")
            waiter = loop.create_future(); self._queue.append((waiter, principal)); self._queued_by_principal[principal] += 1
        try:
            await asyncio.wait_for(waiter, timeout=self.queue_timeout_seconds)
        except TimeoutError as exc:
            async with self._lock: self._remove_waiter_unlocked(waiter, principal)
            raise OverloadRejected(f"{self.name} queue wait timed out", retry_after=5, reason="queue_timeout") from exc
        except asyncio.CancelledError:
            async with self._lock: self._remove_waiter_unlocked(waiter, principal)
            raise
        except OverloadRejected:
            raise

    def _remove_waiter_unlocked(self, waiter: asyncio.Future[None], principal: str) -> None:
        for index, item in enumerate(self._queue):
            if item[0] is waiter:
                del self._queue[index]; self._queued_by_principal[principal] -= 1
                if self._queued_by_principal[principal] <= 0: self._queued_by_principal.pop(principal, None)
                break

    def _drain_unlocked(self, *, retry_after: int) -> None:
        while self._queue:
            waiter, principal = self._queue.popleft(); self._queued_by_principal[principal] -= 1
            if self._queued_by_principal[principal] <= 0: self._queued_by_principal.pop(principal, None)
            if not waiter.done():
                waiter.set_exception(OverloadRejected(f"{self.name} circuit breaker opened", retry_after=retry_after, reason="circuit_open"))

    def _wake_unlocked(self) -> None:
        if self._breaker_state_unlocked() == "open": return
        while self._active < self.max_concurrent and self._queue:
            waiter, principal = self._queue.popleft(); self._queued_by_principal[principal] -= 1
            if self._queued_by_principal[principal] <= 0: self._queued_by_principal.pop(principal, None)
            if waiter.cancelled() or waiter.done(): continue
            self._active += 1; waiter.set_result(None)

    async def _release(self) -> None:
        async with self._lock:
            self._active = max(0, self._active - 1)
            if self._half_open_inflight: self._half_open_inflight = False
            self._wake_unlocked()

    async def record_outcome(self, *, failed: bool) -> None:
        async with self._lock:
            state = self._breaker_state_unlocked()
            if failed:
                self._consecutive_failures += 1
                if state == "half_open" or self._consecutive_failures >= self.breaker_threshold:
                    self._opened_at = monotonic(); self._drain_unlocked(retry_after=max(1, int(self.breaker_cooldown_seconds)))
            else:
                self._consecutive_failures = 0; self._opened_at = 0.0; self._half_open_inflight = False

    @asynccontextmanager
    async def slot(self, principal: str):
        await self._acquire(principal)
        try: yield
        finally: await self._release()


def principal_from_authorization(value: str | None) -> str:
    raw = (value or "").strip()
    if not raw: return "anonymous"
    return hashlib.sha256(raw.encode("utf-8", errors="ignore")).hexdigest()[:24]
