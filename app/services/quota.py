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


def day_start(now: datetime | None = None) -> datetime:
    value = now or datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.replace(hour=0, minute=0, second=0, microsecond=0)


def _sync_measured_policy(db: Session, quota: UserQuota, settings: Settings) -> None:
    try:
        from app.services.measured_plans import active_catalog
        catalog = active_catalog(db)
        policy = (catalog.catalog or {}).get(quota.plan) if catalog is not None else None
        if isinstance(policy, dict):
            quota.monthly_compute_seconds_limit = max(1, int(policy.get("monthly_cpu_seconds") or quota.monthly_compute_seconds_limit))
            quota.max_concurrent_inference = min(
                max(1, int(policy.get("max_concurrent_inference") or quota.max_concurrent_inference)),
                max(1, int(settings.max_concurrent_generations)),
            )
            quota.max_concurrent_jobs = min(
                max(1, int(policy.get("max_concurrent_jobs") or quota.max_concurrent_jobs)),
                max(1, int(settings.default_max_concurrent_jobs)),
            )
    except ImportError:
        return


def _sync_canonical_entitlement(db: Session, user: User, settings: Settings, quota: UserQuota) -> UserQuota:
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


def _request_units_since(db: Session, user_id: str, settings: Settings, since: datetime) -> int:
    from app.services.measured_plans import request_unit_weights
    weights = request_unit_weights(settings)
    fallback = weights.get("work", 2)
    rows = db.execute(
        select(UsageEvent.mode, func.count(UsageEvent.id))
        .where(
            UsageEvent.user_id == user_id,
            UsageEvent.created_at >= since,
            UsageEvent.success.is_(True),
        )
        .group_by(UsageEvent.mode)
    ).all()
    return sum(int(count or 0) * int(weights.get(str(mode), fallback)) for mode, count in rows)


def request_unit_usage(db: Session, user: User, settings: Settings, *, now: datetime | None = None) -> dict:
    from app.services.measured_plans import plan_policy, request_unit_weights
    quota = get_or_create_quota(db, user, settings)
    policy = plan_policy(db, settings, quota.plan) or {}
    monthly_limit = max(0, int(policy.get("monthly_request_units") or 0))
    daily_limit = max(0, int(policy.get("daily_request_units") or 0))
    monthly_used = _request_units_since(db, user.id, settings, month_start(now))
    daily_used = _request_units_since(db, user.id, settings, day_start(now))
    return {
        "plan": quota.plan,
        "monthly_request_units_limit": monthly_limit,
        "monthly_request_units_used": monthly_used,
        "monthly_request_units_remaining": max(0, monthly_limit - monthly_used),
        "daily_request_units_limit": daily_limit,
        "daily_request_units_used": daily_used,
        "daily_request_units_remaining": max(0, daily_limit - daily_used),
        "request_unit_weights": request_unit_weights(settings),
    }


def _inferred_channel(reserve_seconds: int) -> str:
    if reserve_seconds in {15, 30}:
        return "fast"
    if reserve_seconds in {60, 120}:
        return "work"
    return "deep" if reserve_seconds >= 180 else "work"


def _ensure_request_units_available(db: Session, user: User, settings: Settings, plan: str, mode: str) -> None:
    from app.services.measured_plans import plan_policy, request_unit_weights
    policy = plan_policy(db, settings, plan) or {}
    weights = request_unit_weights(settings)
    unit_cost = int(weights.get(mode, weights.get("work", 2)))
    monthly_limit = max(0, int(policy.get("monthly_request_units") or 0))
    daily_limit = max(0, int(policy.get("daily_request_units") or 0))
    if monthly_limit:
        used = _request_units_since(db, user.id, settings, month_start())
        if used + unit_cost > monthly_limit:
            raise QuotaExceededError("Monthly request limit exhausted for this plan")
    if daily_limit:
        used = _request_units_since(db, user.id, settings, day_start())
        if used + unit_cost > daily_limit:
            raise QuotaExceededError("Daily fair-use request limit reached; try again after 00:00 UTC")


def ensure_compute_available(db: Session, user: User, settings: Settings, *, reserve_seconds: int = 0, channel: str | None = None) -> UserQuota:
    quota = get_or_create_quota(db, user, settings)
    reserve_seconds = max(0, int(reserve_seconds))
    used = compute_seconds_used(db, user.id)
    if used + reserve_seconds > quota.monthly_compute_seconds_limit:
        raise QuotaExceededError("Monthly local compute budget exhausted")

    # Request units represent answer complexity, not the transport channel. API
    # calls therefore cost the same Fast/Work/Deep units as UI calls.
    request_mode = _inferred_channel(reserve_seconds)
    _ensure_request_units_available(db, user, settings, quota.plan, request_mode)

    scheduler_channel = channel or request_mode
    try:
        from app.services.measured_plans import current_channel_override
        scheduler_channel = channel or current_channel_override() or scheduler_channel
    except ImportError:
        pass

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
            try:
                ensure_channel_budget(db, user, settings, scheduler_channel, reserve_cost)
            except RuntimeError as exc:
                raise QuotaExceededError(str(exc)) from exc
    except ImportError:
        pass

    try:
        from app.services.resource_governor import set_inference_scheduler_context
        priority = scheduler_channel if scheduler_channel in {"fast", "work", "deep", "api", "background"} else request_mode
        set_inference_scheduler_context(
            priority_class=priority,
            plan=quota.plan,
            principal=user.id,
            channel=scheduler_channel,
        )
    except ImportError:
        pass
    return quota
