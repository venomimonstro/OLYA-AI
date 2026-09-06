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


def _inferred_channel(reserve_seconds: int) -> str:
    # Chat reserve contracts are 15/60/180 seconds and may be doubled by strict
    # verification. Keep each doubled value in the originating fairness lane.
    if reserve_seconds in {15, 30}:
        return "fast"
    if reserve_seconds in {60, 120}:
        return "work"
    return "deep" if reserve_seconds >= 180 else "work"


def ensure_compute_available(
    db: Session,
    user: User,
    settings: Settings,
    *,
    reserve_seconds: int = 0,
    channel: str | None = None,
) -> UserQuota:
    quota = get_or_create_quota(db, user, settings)
    reserve_seconds = max(0, int(reserve_seconds))
    used = compute_seconds_used(db, user.id)
    if used + reserve_seconds > quota.monthly_compute_seconds_limit:
        raise QuotaExceededError("Monthly local compute budget exhausted")

    try:
        from app.services.commerce import measured_user_resources, price_resource_ms
        from app.services.measured_plans import ensure_channel_budget, plan_policy

        policy = plan_policy(db, settings, quota.plan)
        if policy is not None:
            measured = measured_user_resources(db, user.id, settings)
            reserve_cost = price_resource_ms(settings, "cpu", reserve_seconds * 1000)
            total_budget = max(0, int(policy.get("resource_budget_microunits") or 0))
            if total_budget and measured["total_cost_microunits"] + reserve_cost > total_budget:
                raise QuotaExceededError("Monthly measured resource budget exhausted")
            fairness_channel = channel or _inferred_channel(reserve_seconds)
            try:
                ensure_channel_budget(db, user, settings, fairness_channel, reserve_cost)
            except RuntimeError as exc:
                raise QuotaExceededError(str(exc)) from exc
    except ImportError:
        pass
    return quota
