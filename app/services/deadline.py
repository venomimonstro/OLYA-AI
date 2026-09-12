from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass
from time import monotonic


class DeadlineExceededError(RuntimeError):
    pass


@dataclass(frozen=True)
class DeadlineBudget:
    started_at: float
    deadline_at: float
    budget_seconds: float
    source: str

    def remaining_seconds(self) -> float:
        return max(0.0, self.deadline_at - monotonic())

    @property
    def expired(self) -> bool:
        return self.remaining_seconds() <= 0.0


_DEADLINE: ContextVar[DeadlineBudget | None] = ContextVar("x1_request_deadline", default=None)
_HEADER = "x-x1-deadline-ms"
_MIN_SECONDS = 1.0
_MAX_SECONDS = 900.0


def begin_deadline_from_headers(headers, *, default_seconds: float, source: str) -> DeadlineBudget:
    """Start one monotonic budget for the current request/task.

    Client budgets may shorten the server budget but can never extend it. A
    monotonic clock makes the budget immune to wall-clock changes.
    """
    server_budget = max(_MIN_SECONDS, min(_MAX_SECONDS, float(default_seconds)))
    requested: float | None = None
    try:
        raw = headers.get(_HEADER) if headers is not None else None
    except AttributeError:
        raw = None
    if raw not in {None, ""}:
        try:
            milliseconds = int(str(raw).strip())
        except (TypeError, ValueError) as exc:
            raise ValueError("X-X1-Deadline-Ms must be an integer") from exc
        if milliseconds < int(_MIN_SECONDS * 1000) or milliseconds > int(_MAX_SECONDS * 1000):
            raise ValueError("X-X1-Deadline-Ms must be between 1000 and 900000")
        requested = milliseconds / 1000.0
    budget = min(server_budget, requested) if requested is not None else server_budget
    now = monotonic()
    row = DeadlineBudget(started_at=now, deadline_at=now + budget, budget_seconds=budget, source=str(source or "request"))
    _DEADLINE.set(row)
    return row


def current_deadline() -> DeadlineBudget | None:
    return _DEADLINE.get()


def remaining_seconds() -> float | None:
    row = current_deadline()
    return None if row is None else row.remaining_seconds()


def checkpoint(stage: str) -> float | None:
    row = current_deadline()
    if row is None:
        return None
    remaining = row.remaining_seconds()
    if remaining <= 0:
        raise DeadlineExceededError(f"Request deadline exceeded before {stage}")
    return remaining


def clamp_timeout_seconds(timeout_seconds: float, *, stage: str, minimum: float = 0.05) -> float:
    local = max(float(minimum), float(timeout_seconds))
    remaining = checkpoint(stage)
    if remaining is None:
        return local
    if remaining < minimum:
        raise DeadlineExceededError(f"Request deadline exceeded before {stage}")
    return max(float(minimum), min(local, remaining))


def metadata() -> dict:
    row = current_deadline()
    if row is None:
        return {"active": False}
    return {
        "active": True,
        "budget_seconds": row.budget_seconds,
        "remaining_seconds": round(row.remaining_seconds(), 3),
        "source": row.source,
        "expired": row.expired,
    }
