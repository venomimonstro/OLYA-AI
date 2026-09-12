from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from time import monotonic


class ResourceBusyError(RuntimeError):
    pass


_PRIORITY_BASE = {
    "fast": 0.0,
    "interactive": 4.0,
    "work": 10.0,
    "api": 12.0,
    "deep": 20.0,
    "background": 30.0,
}
_PLAN_BOOST = {
    "free": 0.0,
    "x1": -1.0,
    "pro": -2.0,
    "max": -3.0,
    "business": -4.0,
}


@dataclass(eq=False)
class _Waiter:
    sequence: int
    enqueued_at: float
    priority_class: str
    plan: str
    principal: str
    channel: str


class ResourceGovernor:
    """Bounded priority/fair admission for scarce local inference.

    Priority only changes queue ordering. It never bypasses max_concurrent,
    max_queue, per-user governors or quota checks. Waiting time continuously
    improves a request's score, so lower-priority Deep/background work cannot
    starve behind an endless stream of new interactive requests.
    """

    def __init__(self, max_concurrent: int = 1, max_queue: int = 64, wait_timeout_seconds: float = 120.0) -> None:
        if max_concurrent < 1:
            raise ValueError("max_concurrent must be >= 1")
        if max_queue < 0:
            raise ValueError("max_queue must be >= 0")
        if wait_timeout_seconds <= 0:
            raise ValueError("wait_timeout_seconds must be > 0")
        self.max_concurrent = int(max_concurrent)
        self.max_queue = int(max_queue)
        self.wait_timeout_seconds = float(wait_timeout_seconds)
        self._active = 0
        self._sequence = 0
        self._waiters: list[_Waiter] = []
        self._condition = asyncio.Condition()
        self._grants_by_class: dict[str, int] = {}
        self._timeouts = 0
        self._rejections = 0

    @property
    def waiting(self) -> int:
        return len(self._waiters)

    @property
    def active(self) -> int:
        return self._active

    @staticmethod
    def _normalize_priority(value: str) -> str:
        name = str(value or "work").strip().lower()
        return name if name in _PRIORITY_BASE else "work"

    @staticmethod
    def _normalize_plan(value: str) -> str:
        name = str(value or "free").strip().lower()
        return name if name in _PLAN_BOOST else "free"

    def _score(self, waiter: _Waiter, now: float) -> tuple[float, int]:
        # One priority point is recovered every two seconds waited. Because this
        # term is intentionally unbounded, any admitted waiter eventually outranks
        # newly-arriving work even when it started in the background class.
        age_points = max(0.0, now - waiter.enqueued_at) / 2.0
        score = _PRIORITY_BASE[waiter.priority_class] + _PLAN_BOOST[waiter.plan] - age_points
        return score, waiter.sequence

    def _next_waiter(self) -> _Waiter | None:
        if not self._waiters:
            return None
        now = monotonic()
        return min(self._waiters, key=lambda row: self._score(row, now))

    def snapshot(self) -> dict:
        by_class: dict[str, int] = {}
        by_channel: dict[str, int] = {}
        for row in self._waiters:
            by_class[row.priority_class] = by_class.get(row.priority_class, 0) + 1
            by_channel[row.channel] = by_channel.get(row.channel, 0) + 1
        oldest_wait_seconds = 0.0
        if self._waiters:
            oldest_wait_seconds = max(0.0, monotonic() - min(row.enqueued_at for row in self._waiters))
        return {
            "active": self._active,
            "max_concurrent": self.max_concurrent,
            "waiting": len(self._waiters),
            "max_queue": self.max_queue,
            "waiting_by_priority": by_class,
            "waiting_by_channel": by_channel,
            "oldest_wait_seconds": round(oldest_wait_seconds, 3),
            "grants_by_priority": dict(self._grants_by_class),
            "timeouts": self._timeouts,
            "rejections": self._rejections,
            "aging_seconds_per_point": 2.0,
        }

    @asynccontextmanager
    async def slot(
        self,
        *,
        priority_class: str = "work",
        plan: str = "free",
        principal: str = "",
        channel: str = "inference",
    ):
        priority_class = self._normalize_priority(priority_class)
        plan = self._normalize_plan(plan)
        waiter: _Waiter | None = None
        acquired = False
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.wait_timeout_seconds

        async with self._condition:
            # Preserve low-latency admission when the scheduler is idle. Once
            # anyone is queued, every newcomer joins the same ordering policy.
            if self._active < self.max_concurrent and not self._waiters:
                self._active += 1
                acquired = True
                self._grants_by_class[priority_class] = self._grants_by_class.get(priority_class, 0) + 1
            else:
                if len(self._waiters) >= self.max_queue:
                    self._rejections += 1
                    raise ResourceBusyError("local inference queue is full")
                self._sequence += 1
                waiter = _Waiter(
                    sequence=self._sequence,
                    enqueued_at=monotonic(),
                    priority_class=priority_class,
                    plan=plan,
                    principal=str(principal or ""),
                    channel=str(channel or "inference"),
                )
                self._waiters.append(waiter)

                try:
                    while True:
                        if self._active < self.max_concurrent and self._next_waiter() is waiter:
                            self._waiters.remove(waiter)
                            self._active += 1
                            acquired = True
                            self._grants_by_class[priority_class] = self._grants_by_class.get(priority_class, 0) + 1
                            break
                        remaining = deadline - loop.time()
                        if remaining <= 0:
                            if waiter in self._waiters:
                                self._waiters.remove(waiter)
                            self._timeouts += 1
                            self._condition.notify_all()
                            raise ResourceBusyError("local inference queue wait timed out")
                        try:
                            # Periodic wake-up lets aging change ordering even if
                            # no enqueue/release event occurs during this second.
                            await asyncio.wait_for(self._condition.wait(), timeout=min(1.0, remaining))
                        except TimeoutError:
                            pass
                except BaseException:
                    if not acquired and waiter in self._waiters:
                        self._waiters.remove(waiter)
                        self._condition.notify_all()
                    raise

        try:
            yield
        finally:
            if acquired:
                async with self._condition:
                    self._active = max(0, self._active - 1)
                    self._condition.notify_all()
