from __future__ import annotations

import os


_UNLIMITED_SENTINEL = 2_000_000_000


def testing_unlimited_usage_enabled() -> bool:
    value = str(os.getenv("OLYA_TESTING_UNLIMITED_USAGE", "1") or "1").strip().lower()
    return value not in {"0", "false", "off", "no"}


def install_testing_unlimited_usage_patch() -> None:
    if not testing_unlimited_usage_enabled():
        return

    from app.services import budget_transparency, quota

    current_ensure = quota.ensure_compute_available
    if not getattr(current_ensure, "_olya_testing_unlimited_usage", False):
        def unlimited_ensure_compute_available(
            db,
            user,
            settings,
            *,
            reserve_seconds: int = 0,
            channel: str | None = None,
        ):
            # Temporary test mode: do not reject on request-count, monthly CPU,
            # measured-resource or channel-share budgets. Keep entitlement sync
            # and scheduler context so physical server protection still applies.
            user_quota = quota.get_or_create_quota(db, user, settings)
            request_mode = quota._inferred_channel(max(0, int(reserve_seconds)))
            scheduler_channel = channel or request_mode
            try:
                from app.services.measured_plans import current_channel_override
                scheduler_channel = channel or current_channel_override() or scheduler_channel
            except ImportError:
                pass
            try:
                from app.services.resource_governor import set_inference_scheduler_context
                priority = scheduler_channel if scheduler_channel in {"fast", "work", "deep", "api", "background"} else request_mode
                set_inference_scheduler_context(
                    priority_class=priority,
                    plan=user_quota.plan,
                    principal=user.id,
                    channel=scheduler_channel,
                )
            except ImportError:
                pass
            return user_quota

        unlimited_ensure_compute_available._olya_testing_unlimited_usage = True  # type: ignore[attr-defined]
        quota.ensure_compute_available = unlimited_ensure_compute_available

    current_units = quota.request_unit_usage
    if not getattr(current_units, "_olya_testing_unlimited_usage", False):
        def unlimited_request_unit_usage(db, user, settings, *, now=None):
            data = dict(current_units(db, user, settings, now=now))
            monthly_used = max(0, int(data.get("monthly_request_units_used") or 0))
            daily_used = max(0, int(data.get("daily_request_units_used") or 0))
            data.update(
                {
                    "monthly_request_units_limit": _UNLIMITED_SENTINEL,
                    "monthly_request_units_remaining": _UNLIMITED_SENTINEL,
                    "daily_request_units_limit": _UNLIMITED_SENTINEL,
                    "daily_request_units_remaining": _UNLIMITED_SENTINEL,
                    "testing_unlimited_usage": True,
                    "recorded_monthly_request_units_used": monthly_used,
                    "recorded_daily_request_units_used": daily_used,
                }
            )
            return data

        unlimited_request_unit_usage._olya_testing_unlimited_usage = True  # type: ignore[attr-defined]
        quota.request_unit_usage = unlimited_request_unit_usage

    current_snapshot = budget_transparency.budget_snapshot
    if not getattr(current_snapshot, "_olya_testing_unlimited_usage", False):
        def unlimited_budget_snapshot(*args, **kwargs):
            result = dict(current_snapshot(*args, **kwargs))
            compute = dict(result.get("compute") or {})
            used_seconds = max(0, int(compute.get("used_seconds") or 0))
            recorded_limit = max(0, int(compute.get("limit_seconds") or 0))
            compute.update(
                {
                    "limit_seconds": _UNLIMITED_SENTINEL,
                    "remaining_seconds": _UNLIMITED_SENTINEL,
                    "utilization_percent": 0.0,
                    "warning": None,
                    "testing_unlimited_usage": True,
                    "recorded_limit_seconds": recorded_limit,
                    "recorded_used_seconds": used_seconds,
                }
            )
            result["compute"] = compute
            projection = result.get("projection")
            if isinstance(projection, dict):
                projection = dict(projection)
                projection["fits_remaining_compute"] = True
                projection["remaining_after_reserve_seconds"] = _UNLIMITED_SENTINEL
                projection["testing_unlimited_usage"] = True
                result["projection"] = projection
            result["testing_unlimited_usage"] = True
            return result

        unlimited_budget_snapshot._olya_testing_unlimited_usage = True  # type: ignore[attr-defined]
        budget_transparency.budget_snapshot = unlimited_budget_snapshot
