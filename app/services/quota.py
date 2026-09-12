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


def _sync_measured_policy(db: Session, quota: UserQuota, settings: Settings) -> None:
    try:
        from app.services.measured_plans import active_catalog
        catalog = active_catalog(db)
        policy = (catalog.catalog or {}).get(quota.plan) if catalog is not None else None
        if isinstance(policy, dict):
            quota.monthly_compute_seconds_limit = max(1, int(policy.get("monthly_cpu_seconds") or quota.monthly_compute_seconds_limit))
            quota.max_concurrent_inference = max(1, int(policy.get("max_concurrent_inference") or quota.max_concurrent_inference))
            quota.max_concurrent_jobs = max(1, int(policy.get("max_concurrent_jobs") or quota.max_concurrent_jobs))
    except ImportError:
        return


def _sync_canonical_entitlement(db: Session, user: User, settings: Settings, quota: UserQuota) -> UserQuota:
    """Restore billing/free truth before applying any explicit admin override.

    This makes direct mutations of UserQuota non-authoritative. A paid subscription
    controls its plan; users without an active subscription return to Free.
    """
    try:
        from app.services.billing import reconcile_user_subscription
        from app.services.measured_plans import apply_runtime_plan_to_quota
        sub = reconcile_user_subscription(db, user, settings, quota=quota)
        target_plan = sub.plan if sub is not None and sub.status == "active" else "free"
        quota = apply_runtime_plan_to_quota(db, user, settings, target_plan)
    except ImportError:
        pass
    return quota


def get_or_create_quota(db: Session, user: User, settings: Settings) -> UserQuota:
    quota = db.get(UserQuota, user.id)
    if quota is None:
        quota = UserQuota(
            user_id=user.id,
            plan="free",
            monthly_compute_seconds_limit=settings.default_monthly_compute_seconds,
            max_concurrent_inference=settings.default_max_concurrent_inference,
            max_concurrent_jobs=settings.default_max_concurrent_jobs,
        )
        db.add(quota)
        db.flush()
    quota = _sync_canonical_entitlement(db, user, settings, quota)
    _sync_measured_policy(db, quota, settings)
    try:
        from app.services.admin_user_controls import apply_control_to_quota
        quota = apply_control_to_quota(db, user, settings, quota)
    except ImportError:
        pass
    return quota


def compute_seconds_used(db: Session, user_id: str, now: datetime | None = None) -> int:
    since = month_start(now)
    total_ms = db.scalar(select(func.coalesce(func.sum(UsageEvent.inference_ms), 0)).where(UsageEvent.user_id == user_id, UsageEvent.created_at >= since)) or 0
    return int(total_ms) // 1000


def _inferred_channel(reserve_seconds: int) -> str:
    if reserve_seconds in {15, 30}: return "fast"
    if reserve_seconds in {60, 120}: return "work"
    return "deep" if reserve_seconds >= 180 else "work"


def ensure_compute_available(db: Session, user: User, settings: Settings, *, reserve_seconds: int = 0, channel: str | None = None) -> UserQuota:
    quota = get_or_create_quota(db, user, settings)
    reserve_seconds = max(0, int(reserve_seconds))
    used = compute_seconds_used(db, user.id)
    if used + reserve_seconds > quota.monthly_compute_seconds_limit:
        raise QuotaExceededError("Monthly local compute budget exhausted")
    try:
        from app.services.commerce import measured_user_resources, price_resource_ms
        from app.services.measured_plans import current_channel_override, ensure_channel_budget, plan_policy
        policy = plan_policy(db, settings, quota.plan)
        if policy is not None:
            measured = measured_user_resources(db, user.id, settings)
            reserve_cost = price_resource_ms(settings, "cpu", reserve_seconds * 1000)
            total_budget = max(0, int(policy.get("resource_budget_microunits") or 0))
            if total_budget and measured["total_cost_microunits"] + reserve_cost > total_budget:
                raise QuotaExceededError("Monthly measured resource budget exhausted")
            fairness_channel = channel or current_channel_override() or _inferred_channel(reserve_seconds)
            try: ensure_channel_budget(db, user, settings, fairness_channel, reserve_cost)
            except RuntimeError as exc: raise QuotaExceededError(str(exc)) from exc
    except ImportError:
        pass
    return quota
