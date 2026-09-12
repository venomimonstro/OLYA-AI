from __future__ import annotations

from contextvars import ContextVar, Token

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import MeasuredPlanCatalog, ResourceExpenseEvent, UsageEvent, User, UserQuota
from app.services.commerce import PLAN_POLICIES, price_resource_ms, resource_rates

_channel_override: ContextVar[str | None] = ContextVar("x1_fairness_channel", default=None)
_PLAN_NAMES = ("free", "x1", "pro", "max", "business")


def set_channel_override(channel: str) -> Token:
    return _channel_override.set(channel)


def reset_channel_override(token: Token) -> None:
    _channel_override.reset(token)


def current_channel_override() -> str | None:
    return _channel_override.get()


def request_unit_weights(settings) -> dict[str, int]:
    # Transport-neutral pricing: API and UI cost the same for the same selected
    # Fast/Work/Deep route. API has a separate rate limit and resource share.
    return {
        "fast": max(1, int(settings.request_unit_weight_fast)),
        "work": max(1, int(settings.request_unit_weight_work)),
        "deep": max(1, int(settings.request_unit_weight_deep)),
    }


def request_limits(settings, name: str) -> dict[str, int]:
    if name not in _PLAN_NAMES:
        return {"monthly_request_units": 0, "daily_request_units": 0}
    return {
        "monthly_request_units": max(0, int(getattr(settings, f"plan_monthly_request_units_{name}", 0))),
        "daily_request_units": max(0, int(getattr(settings, f"plan_daily_request_units_{name}", 0))),
    }


def _channel_shares(settings) -> dict[str, float]:
    return {
        "fast": max(0.0, float(settings.plan_share_fast)),
        "work": max(0.0, float(settings.plan_share_work)),
        "deep": max(0.0, float(settings.plan_share_deep)),
        "api": max(0.0, float(settings.plan_share_api)),
        "image_worker": max(0.0, float(settings.plan_share_image)),
        "sandbox": max(0.0, float(settings.plan_share_sandbox)),
    }


def _enrich_policy(settings, name: str, payload: dict) -> dict:
    item = dict(payload)
    item.update(request_limits(settings, name))
    item["request_unit_weights"] = request_unit_weights(settings)
    item["max_concurrent_inference"] = min(
        max(1, int(item.get("max_concurrent_inference") or 1)),
        max(1, int(settings.max_concurrent_generations)),
    )
    item["max_concurrent_jobs"] = min(
        max(1, int(item.get("max_concurrent_jobs") or 1)),
        max(1, int(settings.default_max_concurrent_jobs)),
    )
    shares = item.get("channel_shares")
    if not isinstance(shares, dict) or not shares:
        item["channel_shares"] = _channel_shares(settings)
    return item


def _fallback_policy(settings, name: str) -> dict | None:
    policy = PLAN_POLICIES.get(name)
    if policy is None:
        return None
    payload = {
        "name": policy.name,
        "monthly_cpu_seconds": policy.monthly_cpu_seconds,
        "resource_budget_microunits": policy.monthly_cpu_seconds * settings.commerce_cpu_microunits_per_second,
        "max_concurrent_inference": policy.max_concurrent_inference,
        "max_concurrent_jobs": policy.max_concurrent_jobs,
        "organization_enabled": policy.organization_enabled,
        "channel_shares": _channel_shares(settings),
        "source": "fallback-static-policy",
    }
    return _enrich_policy(settings, name, payload)


def active_catalog(db: Session) -> MeasuredPlanCatalog | None:
    return db.scalar(select(MeasuredPlanCatalog).where(MeasuredPlanCatalog.status == "active").order_by(MeasuredPlanCatalog.version.desc()).limit(1))


def plan_policy(db: Session, settings, name: str) -> dict | None:
    row = active_catalog(db)
    if row is not None:
        payload = (row.catalog or {}).get(name)
        if isinstance(payload, dict):
            return _enrich_policy(settings, name, payload)
    return _fallback_policy(settings, name)


def runtime_plan_catalog(db: Session, settings) -> list[dict]:
    row = active_catalog(db)
    if row is not None and row.catalog:
        result = []
        for name in _PLAN_NAMES:
            payload = row.catalog.get(name)
            if isinstance(payload, dict):
                item = _enrich_policy(settings, name, payload)
                item["catalog_version"] = row.version
                result.append(item)
        if result:
            return result
    rates = resource_rates(settings)
    result = []
    for name in _PLAN_NAMES:
        payload = _fallback_policy(settings, name)
        if payload:
            payload["resource_rates_microunits_per_second"] = rates
            payload["catalog_version"] = None
            result.append(payload)
    return result


def apply_runtime_plan_to_quota(db: Session, user: User, settings, name: str) -> UserQuota:
    policy = plan_policy(db, settings, name)
    if policy is None:
        raise ValueError("Unknown plan")
    quota = db.get(UserQuota, user.id) or UserQuota(user_id=user.id)
    db.add(quota)
    quota.plan = name
    quota.monthly_compute_seconds_limit = int(policy["monthly_cpu_seconds"])
    quota.max_concurrent_inference = max(1, int(policy["max_concurrent_inference"]))
    quota.max_concurrent_jobs = max(1, int(policy["max_concurrent_jobs"]))
    return quota


def _channel_spend(db: Session, user_id: str, settings, channel: str, since) -> int:
    if channel in {"fast", "work", "deep"}:
        cpu_ms = int(db.scalar(select(func.coalesce(func.sum(UsageEvent.inference_ms), 0)).where(UsageEvent.user_id == user_id, UsageEvent.created_at >= since, UsageEvent.mode == channel)) or 0)
        return price_resource_ms(settings, "cpu", cpu_ms)
    if channel == "api":
        return int(db.scalar(select(func.coalesce(func.sum(ResourceExpenseEvent.cost_microunits), 0)).where(ResourceExpenseEvent.user_id == user_id, ResourceExpenseEvent.created_at >= since, ResourceExpenseEvent.source_kind == "api_request")) or 0)
    if channel in {"image_worker", "sandbox"}:
        from app.services.commerce import measured_user_resources
        measured = measured_user_resources(db, user_id, settings)
        return int((measured.get("cost_microunits") or {}).get(channel, 0))
    return 0


def ensure_channel_budget(db: Session, user: User, settings, channel: str, reserve_cost_microunits: int = 0) -> None:
    from app.services.quota import get_or_create_quota, month_start
    quota = get_or_create_quota(db, user, settings)
    policy = plan_policy(db, settings, quota.plan)
    if not policy:
        return
    share = float((policy.get("channel_shares") or {}).get(channel, 0.0))
    if share <= 0:
        return
    total_budget = max(0, int(policy.get("resource_budget_microunits") or 0))
    channel_budget = int(total_budget * share)
    if channel_budget <= 0:
        return
    spent = _channel_spend(db, user.id, settings, channel, month_start())
    if spent + max(0, int(reserve_cost_microunits)) > channel_budget:
        raise RuntimeError(f"Monthly {channel} resource share exhausted")
