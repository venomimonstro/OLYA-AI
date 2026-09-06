from __future__ import annotations

from datetime import datetime, timedelta, timezone
from math import ceil

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AnswerAudit, BetaParticipant, BetaSnapshot, FrustrationEvent, Task, UsageEvent


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value):
    return value if value is None or value.tzinfo else value.replace(tzinfo=timezone.utc)


def _percentile(values, quantile: float) -> int:
    if not values:
        return 0
    vals = sorted(max(0, int(value or 0)) for value in values)
    return vals[max(0, ceil(max(0.0, min(1.0, quantile)) * len(vals)) - 1)]


def _retention(participants, usage_by_user: dict[str, list[datetime]], now: datetime, day: int) -> tuple[int, int]:
    eligible = retained = 0
    for participant in participants:
        enrolled = _aware(participant.enrolled_at)
        if enrolled is None or now < enrolled + timedelta(days=day):
            continue
        eligible += 1
        start = enrolled + timedelta(days=day)
        end = start + timedelta(days=1)
        retained += int(any(start <= event < end for event in usage_by_user.get(participant.user_id, [])))
    return eligible, retained


def calculate_beta_metrics(
    db: Session,
    *,
    cohort: str = "closed-beta-1",
    window_days: int = 30,
    now: datetime | None = None,
) -> dict:
    """Calculate closed-beta telemetry without mutating the database."""
    now = _aware(now) or _now()
    window_days = max(1, min(180, int(window_days)))
    since = now - timedelta(days=window_days)

    participants = list(db.scalars(
        select(BetaParticipant).where(BetaParticipant.cohort == cohort, BetaParticipant.enrolled_at <= now)
    ).all())
    ids = sorted({participant.user_id for participant in participants})
    active_count = sum(participant.state == "active" for participant in participants)
    paused_count = sum(participant.state == "paused" for participant in participants)
    removed_count = sum(participant.state == "removed" for participant in participants)

    usage_window = list(db.scalars(
        select(UsageEvent).where(UsageEvent.user_id.in_(ids), UsageEvent.created_at >= since, UsageEvent.created_at <= now)
    ).all()) if ids else []
    tasks = list(db.scalars(
        select(Task).where(Task.created_by.in_(ids), Task.created_at >= since, Task.created_at <= now)
    ).all()) if ids else []
    frustration = list(db.scalars(
        select(FrustrationEvent).where(
            FrustrationEvent.user_id.in_(ids), FrustrationEvent.created_at >= since, FrustrationEvent.created_at <= now
        )
    ).all()) if ids else []

    earliest = min((_aware(participant.enrolled_at) for participant in participants), default=since)
    retention_since = max(now - timedelta(days=180), earliest or since)
    retention_usage = list(db.scalars(
        select(UsageEvent).where(
            UsageEvent.user_id.in_(ids), UsageEvent.created_at >= retention_since, UsageEvent.created_at <= now
        )
    ).all()) if ids else []
    usage_by_user: dict[str, list[datetime]] = {user_id: [] for user_id in ids}
    for event in retention_usage:
        created = _aware(event.created_at)
        if created is not None:
            usage_by_user.setdefault(event.user_id, []).append(created)

    activated = sum(bool(usage_by_user.get(participant.user_id)) for participant in participants)
    d1e, d1r = _retention(participants, usage_by_user, now, 1)
    d7e, d7r = _retention(participants, usage_by_user, now, 7)
    d30e, d30r = _retention(participants, usage_by_user, now, 30)

    success = sum(bool(event.success) for event in usage_window)
    compute_ms = sum(max(0, int(event.inference_ms or 0)) for event in usage_window)
    completed = sum(task.status == "completed" for task in tasks)
    cpu_minutes = compute_ms / 60_000.0

    request_ids = {event.request_id for event in usage_window if event.request_id and event.success}
    quality_rows = list(db.scalars(
        select(AnswerAudit).where(AnswerAudit.request_id.in_(request_ids))
    ).all()) if request_ids else []
    quality_by_request = {row.request_id: row.status for row in quality_rows}
    supported_requests = sum(quality_by_request.get(request_id) == "supported" for request_id in request_ids)
    quality_nonfailed_requests = sum(
        quality_by_request.get(request_id) in {"checked", "supported"} for request_id in request_ids
    )

    request_count = len(usage_window)
    metrics = {
        "d1_retention": 0 if not d1e else round(d1r / d1e, 4),
        "d7_retention": 0 if not d7e else round(d7r / d7e, 4),
        "d30_retention": 0 if not d30e else round(d30r / d30e, 4),
        "request_success_rate": 0 if not request_count else round(success / request_count, 4),
        "task_completion_rate": 0 if not tasks else round(completed / len(tasks), 4),
        "frustration_per_request": 0 if not request_count else round(len(frustration) / request_count, 4),
        "completed_task_success_per_cpu_minute": 0 if cpu_minutes <= 0 else round(completed / cpu_minutes, 6),
        "verified_request_success_per_cpu_minute": 0 if cpu_minutes <= 0 else round(supported_requests / cpu_minutes, 6),
        "quality_nonfailed_request_rate": 0 if not request_ids else round(quality_nonfailed_requests / len(request_ids), 4),
        "quality_supported_request_rate": 0 if not request_ids else round(supported_requests / len(request_ids), 4),
        "verified_task_success_per_cpu_minute": 0 if cpu_minutes <= 0 else round(completed / cpu_minutes, 6),
    }
    readiness = {
        "participant_target_met": 50 <= len(participants) <= 100,
        "task_target_met": len(tasks) >= 500,
        "capacity_recalibration_ready": 50 <= len(participants) <= 100 and len(tasks) >= 500,
    }
    return {
        "cohort": cohort,
        "window_days": window_days,
        "enrolled_count": len(participants),
        "active_participant_count": active_count,
        "paused_participant_count": paused_count,
        "removed_participant_count": removed_count,
        "activated_count": activated,
        "d1_eligible_count": d1e,
        "d1_retained_count": d1r,
        "d7_eligible_count": d7e,
        "d7_retained_count": d7r,
        "d30_eligible_count": d30e,
        "d30_retained_count": d30r,
        "request_count": request_count,
        "success_count": success,
        "task_count": len(tasks),
        "completed_task_count": completed,
        "frustration_count": len(frustration),
        "compute_minutes_total": round(cpu_minutes, 4),
        "compute_minutes_per_active_user": 0 if not activated else round(cpu_minutes / activated, 4),
        "p50_duration_ms": _percentile([event.duration_ms for event in usage_window], 0.50),
        "p95_duration_ms": _percentile([event.duration_ms for event in usage_window], 0.95),
        "p99_duration_ms": _percentile([event.duration_ms for event in usage_window], 0.99),
        "p50_queue_ms": _percentile([event.queue_ms for event in usage_window], 0.50),
        "p95_queue_ms": _percentile([event.queue_ms for event in usage_window], 0.95),
        "p99_queue_ms": _percentile([event.queue_ms for event in usage_window], 0.99),
        "metrics": metrics,
        "readiness": readiness,
        "measured_at": now,
    }


def build_beta_snapshot(
    db: Session,
    *,
    cohort: str = "closed-beta-1",
    window_days: int = 30,
    now: datetime | None = None,
) -> BetaSnapshot:
    data = calculate_beta_metrics(db, cohort=cohort, window_days=window_days, now=now)
    row = BetaSnapshot(
        cohort=data["cohort"],
        window_days=data["window_days"],
        enrolled_count=data["enrolled_count"],
        activated_count=data["activated_count"],
        d1_eligible_count=data["d1_eligible_count"],
        d1_retained_count=data["d1_retained_count"],
        d7_eligible_count=data["d7_eligible_count"],
        d7_retained_count=data["d7_retained_count"],
        request_count=data["request_count"],
        success_count=data["success_count"],
        task_count=data["task_count"],
        completed_task_count=data["completed_task_count"],
        frustration_count=data["frustration_count"],
        compute_minutes_total=data["compute_minutes_total"],
        compute_minutes_per_active_user=data["compute_minutes_per_active_user"],
        p95_duration_ms=data["p95_duration_ms"],
        p95_queue_ms=data["p95_queue_ms"],
        metrics={
            **data["metrics"],
            "d30_eligible_count": data["d30_eligible_count"],
            "d30_retained_count": data["d30_retained_count"],
            "p50_duration_ms": data["p50_duration_ms"],
            "p99_duration_ms": data["p99_duration_ms"],
            "p50_queue_ms": data["p50_queue_ms"],
            "p99_queue_ms": data["p99_queue_ms"],
            "active_participant_count": data["active_participant_count"],
            "paused_participant_count": data["paused_participant_count"],
            "removed_participant_count": data["removed_participant_count"],
        },
        readiness=data["readiness"],
    )
    db.add(row)
    db.flush()
    return row


def snapshot_dict(snapshot: BetaSnapshot) -> dict:
    data = {key: getattr(snapshot, key) for key in (
        "id", "cohort", "window_days", "enrolled_count", "activated_count",
        "d1_eligible_count", "d1_retained_count", "d7_eligible_count", "d7_retained_count",
        "request_count", "success_count", "task_count", "completed_task_count", "frustration_count",
        "compute_minutes_total", "compute_minutes_per_active_user", "p95_duration_ms", "p95_queue_ms",
        "metrics", "readiness", "created_at",
    )}
    metrics = dict(snapshot.metrics or {})
    data.update({
        "d30_eligible_count": int(metrics.get("d30_eligible_count") or 0),
        "d30_retained_count": int(metrics.get("d30_retained_count") or 0),
        "p50_duration_ms": int(metrics.get("p50_duration_ms") or 0),
        "p99_duration_ms": int(metrics.get("p99_duration_ms") or 0),
        "p50_queue_ms": int(metrics.get("p50_queue_ms") or 0),
        "p99_queue_ms": int(metrics.get("p99_queue_ms") or 0),
        "active_participant_count": int(metrics.get("active_participant_count") or 0),
        "paused_participant_count": int(metrics.get("paused_participant_count") or 0),
        "removed_participant_count": int(metrics.get("removed_participant_count") or 0),
    })
    return data
