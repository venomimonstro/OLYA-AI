from __future__ import annotations

from datetime import datetime, timedelta, timezone
from statistics import median

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import BillingSubscription, FrustrationEvent, ProductEvent, Task, UsageEvent, User, UserOnboarding
from app.services.operations_analytics import operations_summary


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _retention(users: list[User], usage_by_user: dict[str, list[datetime]], day: int, now: datetime) -> dict:
    eligible = 0
    retained = 0
    for user in users:
        created = _aware(user.created_at)
        if created + timedelta(days=day + 1) > now:
            continue
        eligible += 1
        start = created + timedelta(days=day)
        end = start + timedelta(days=1)
        if any(start <= stamp < end for stamp in usage_by_user.get(user.id, [])):
            retained += 1
    return {"eligible": eligible, "retained": retained, "rate": round(retained / max(1, eligible), 4)}


def product_analytics(db: Session, *, days: int = 30, monthly_server_cost_rub: float = 4000.0) -> dict:
    days = max(1, min(int(days), 365))
    now = _now()
    since = now - timedelta(days=days)

    cohort = list(db.scalars(select(User).where(User.created_at >= since).order_by(User.created_at)).all())
    cohort_ids = [row.id for row in cohort]
    onboarding = list(db.scalars(select(UserOnboarding).where(UserOnboarding.user_id.in_(cohort_ids))).all()) if cohort_ids else []
    onboarding_by_user = {row.user_id: row for row in onboarding}

    activated = [row for row in onboarding if row.completed_at is not None]
    first_value_seconds = []
    for user in cohort:
        row = onboarding_by_user.get(user.id)
        if row is None or row.completed_at is None:
            continue
        seconds = max(0.0, (_aware(row.completed_at) - _aware(user.created_at)).total_seconds())
        first_value_seconds.append(seconds)

    usage_rows = list(
        db.scalars(
            select(UsageEvent)
            .where(UsageEvent.user_id.in_(cohort_ids), UsageEvent.success.is_(True))
            .order_by(UsageEvent.created_at)
        ).all()
    ) if cohort_ids else []
    usage_by_user: dict[str, list[datetime]] = {}
    for row in usage_rows:
        usage_by_user.setdefault(row.user_id, []).append(_aware(row.created_at))

    task_rows = list(db.scalars(select(Task).where(Task.created_at >= since)).all())
    terminal = [row for row in task_rows if row.status in {"completed", "failed", "cancelled"}]
    completed = [row for row in terminal if row.status == "completed"]

    frustration = int(db.scalar(select(func.count()).select_from(FrustrationEvent).where(FrustrationEvent.created_at >= since)) or 0)
    successful_usage = int(db.scalar(select(func.count()).select_from(UsageEvent).where(UsageEvent.created_at >= since, UsageEvent.success.is_(True))) or 0)

    total_users = int(db.scalar(select(func.count()).select_from(User)) or 0)
    active_paid = int(
        db.scalar(
            select(func.count()).select_from(BillingSubscription).where(
                BillingSubscription.status == "active",
                BillingSubscription.current_period_end > now,
                BillingSubscription.plan != "free",
            )
        )
        or 0
    )
    paid_by_plan = dict(
        db.execute(
            select(BillingSubscription.plan, func.count())
            .where(
                BillingSubscription.status == "active",
                BillingSubscription.current_period_end > now,
                BillingSubscription.plan != "free",
            )
            .group_by(BillingSubscription.plan)
        ).all()
    )

    event_counts = dict(
        db.execute(
            select(ProductEvent.event_name, func.count())
            .where(ProductEvent.created_at >= since)
            .group_by(ProductEvent.event_name)
        ).all()
    )

    ops = operations_summary(db, window_hours=days * 24, monthly_server_cost_rub=monthly_server_cost_rub)
    first_value_median = round(float(median(first_value_seconds)), 1) if first_value_seconds else None

    return {
        "generated_at": now.isoformat(),
        "window_days": days,
        "privacy": {
            "stores_message_content": False,
            "stores_prompt_content": False,
            "aggregation_sources": ["user_onboarding", "product_events", "usage_events", "tasks", "frustration_events", "billing_subscriptions", "resource_economics"],
        },
        "activation": {
            "registered": len(cohort),
            "activated": len(activated),
            "activation_rate": round(len(activated) / max(1, len(cohort)), 4),
            "median_seconds_to_first_value": first_value_median,
        },
        "retention": {
            "d1": _retention(cohort, usage_by_user, 1, now),
            "d7": _retention(cohort, usage_by_user, 7, now),
        },
        "task_success": {
            "created": len(task_rows),
            "terminal": len(terminal),
            "completed": len(completed),
            "terminal_success_rate": round(len(completed) / max(1, len(terminal)), 4),
        },
        "frustration": {
            "events": frustration,
            "per_successful_request": round(frustration / max(1, successful_usage), 4),
        },
        "conversion": {
            "total_users": total_users,
            "active_paid_users": active_paid,
            "active_paid_rate": round(active_paid / max(1, total_users), 4),
            "active_paid_by_plan": {str(key): int(value) for key, value in paid_by_plan.items()},
        },
        "event_counts": {str(key): int(value) for key, value in event_counts.items()},
        "economics": ops.get("economics", {}),
        "resource_efficiency": {
            "cpu_seconds_per_success": ops.get("traffic", {}).get("cpu_seconds_per_success"),
            "context_efficiency_ratio": ops.get("traffic", {}).get("context_efficiency_ratio"),
            "compute_ms_per_successful_answer": ops.get("compute_economics", {}).get("compute_ms_per_successful_answer"),
            "waste_rate": ops.get("compute_economics", {}).get("waste_rate"),
        },
    }
