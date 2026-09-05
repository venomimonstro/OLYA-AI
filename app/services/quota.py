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
    quota = UserQuota(user_id=user.id, plan="free", monthly_compute_seconds_limit=settings.default_monthly_compute_seconds,
                      max_concurrent_inference=settings.default_max_concurrent_inference, max_concurrent_jobs=settings.default_max_concurrent_jobs)
    db.add(quota); db.flush(); return quota


def compute_seconds_used(db: Session, user_id: str, now: datetime | None = None) -> int:
    since = month_start(now)
    total_ms = db.scalar(select(func.coalesce(func.sum(UsageEvent.inference_ms), 0)).where(UsageEvent.user_id == user_id, UsageEvent.created_at >= since)) or 0
    return int(total_ms) // 1000


def ensure_compute_available(db: Session, user: User, settings: Settings, *, reserve_seconds: int = 0) -> UserQuota:
    quota = get_or_create_quota(db, user, settings)
    reserve_seconds = max(0, int(reserve_seconds))
    used = compute_seconds_used(db, user.id)
    if used + reserve_seconds >= quota.monthly_compute_seconds_limit:
        raise QuotaExceededError("Monthly local compute budget exhausted")
    # A second admission gate prices all measured resources, not just model CPU.
    # Runtime import avoids coupling the core quota module to optional commerce
    # startup while keeping the plan ledger authoritative when it is installed.
    try:
        from app.services.commerce import PLAN_POLICIES, measured_user_resources, plan_resource_budget, price_resource_ms
        policy = PLAN_POLICIES.get(quota.plan)
        if policy is not None:
            measured = measured_user_resources(db, user.id, settings)
            reserve_cost = price_resource_ms(settings, "cpu", reserve_seconds * 1000)
            if measured["total_cost_microunits"] + reserve_cost > plan_resource_budget(settings, policy):
                raise QuotaExceededError("Monthly measured resource budget exhausted")
    except ImportError:
        # The structural route contract will flag a missing commerce subsystem;
        # ordinary chat must still fail only on its legacy CPU hard limit.
        pass
    return quota
