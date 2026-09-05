from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models import UsageEvent, User, UserQuota


class QuotaExceededError(RuntimeError):
    pass


def month_start(now: datetime | None = None) -> datetime:
    value = now or datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def get_or_create_quota(db: Session, user: User, settings: Settings) -> UserQuota:
    quota = db.get(UserQuota, user.id)
    if quota is not None:
        return quota
    quota = UserQuota(
        user_id=user.id,
        plan="free",
        monthly_compute_seconds_limit=settings.default_monthly_compute_seconds,
        max_concurrent_inference=settings.default_max_concurrent_inference,
        max_concurrent_jobs=settings.default_max_concurrent_jobs,
    )
    db.add(quota)
    db.flush()
    return quota


def compute_seconds_used(db: Session, user_id: str, now: datetime | None = None) -> int:
    since = month_start(now)
    total_ms = db.scalar(
        select(func.coalesce(func.sum(UsageEvent.inference_ms), 0)).where(
            UsageEvent.user_id == user_id,
            UsageEvent.created_at >= since,
        )
    ) or 0
    return int(total_ms) // 1000


def ensure_compute_available(db: Session, user: User, settings: Settings, *, reserve_seconds: int = 0) -> UserQuota:
    quota = get_or_create_quota(db, user, settings)
    used = compute_seconds_used(db, user.id)
    if used + max(0, reserve_seconds) >= quota.monthly_compute_seconds_limit:
        raise QuotaExceededError("Monthly local compute budget exhausted")
    return quota
