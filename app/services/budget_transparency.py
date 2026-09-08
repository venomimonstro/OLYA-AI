from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models import ComputeBreakdownEvent, ImageGeneration, ProjectSandboxRun, ResearchRun, UsageEvent, User
from app.services.commerce import measured_user_resources, price_resource_ms
from app.services.quota import compute_seconds_used, get_or_create_quota, month_start


WARNING_LEVELS = ((95, "critical"), (85, "high"), (70, "notice"))
MODE_RESERVE_SECONDS = {"fast": 15, "work": 60, "deep": 180}


def _pct(used: int, limit: int) -> float:
    return 0.0 if limit <= 0 else round(min(100.0, max(0.0, used / limit * 100.0)), 2)


def warning_for(utilization_percent: float) -> dict[str, Any] | None:
    for threshold, level in WARNING_LEVELS:
        if utilization_percent >= threshold:
            return {
                "level": level,
                "threshold_percent": threshold,
                "message": (
                    "Вычислительный бюджет почти исчерпан. Более тяжёлые режимы могут быть недоступны."
                    if threshold >= 95
                    else "Вычислительный бюджет заканчивается. Для простых задач используйте Fast/Auto."
                    if threshold >= 85
                    else "Использовано больше 70% месячного вычислительного бюджета."
                ),
            }
    return None


def estimated_reserve_seconds(mode: str, verification_extra_budget: int = 0) -> int:
    base = MODE_RESERVE_SECONDS.get(mode, MODE_RESERVE_SECONDS["work"])
    return base * (1 + max(0, min(2, int(verification_extra_budget))))


def _chat_breakdown(db: Session, user_id: str, since: datetime) -> dict[str, Any]:
    rows = list(
        db.scalars(
            select(ComputeBreakdownEvent).where(
                ComputeBreakdownEvent.user_id == user_id,
                ComputeBreakdownEvent.created_at >= since,
            )
        ).all()
    )
    by_mode: dict[str, dict[str, int]] = {
        key: {"requests": 0, "primary_ms": 0, "critic_ms": 0, "repair_ms": 0, "total_ms": 0, "wasted_ms": 0}
        for key in ("fast", "work", "deep")
    }
    critic_ms = repair_ms = wasted_ms = successful = 0
    for row in rows:
        bucket = by_mode.setdefault(row.mode, {"requests": 0, "primary_ms": 0, "critic_ms": 0, "repair_ms": 0, "total_ms": 0, "wasted_ms": 0})
        bucket["requests"] += 1
        bucket["primary_ms"] += int(row.primary_ms or 0)
        bucket["critic_ms"] += int(row.critic_ms or 0)
        bucket["repair_ms"] += int(row.repair_ms or 0)
        bucket["total_ms"] += int(row.total_inference_ms or 0)
        bucket["wasted_ms"] += int(row.wasted_ms or 0)
        critic_ms += int(row.critic_ms or 0)
        repair_ms += int(row.repair_ms or 0)
        wasted_ms += int(row.wasted_ms or 0)
        successful += int(bool(row.success))
    total_ms = sum(item["total_ms"] for item in by_mode.values())
    return {
        "by_mode": by_mode,
        "critic_ms": critic_ms,
        "repair_ms": repair_ms,
        "verification_ms": critic_ms + repair_ms,
        "wasted_ms": wasted_ms,
        "requests": len(rows),
        "successful_requests": successful,
        "total_ms": total_ms,
        "compute_ms_per_success": round(total_ms / max(1, successful), 2),
    }


def budget_snapshot(
    db: Session,
    user: User,
    settings: Settings,
    *,
    projected_mode: str | None = None,
    projected_verification_extra: int = 0,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = now or datetime.now(timezone.utc)
    since = month_start(current)
    quota = get_or_create_quota(db, user, settings)
    used_seconds = compute_seconds_used(db, user.id, current)
    limit_seconds = max(1, int(quota.monthly_compute_seconds_limit))
    remaining_seconds = max(0, limit_seconds - used_seconds)
    utilization = _pct(used_seconds, limit_seconds)
    measured = measured_user_resources(db, user.id, settings, current)
    chat = _chat_breakdown(db, user.id, since)

    research_runs = int(
        db.scalar(
            select(func.count()).select_from(ResearchRun).where(
                ResearchRun.user_id == user.id,
                ResearchRun.created_at >= since,
            )
        ) or 0
    )
    images = int(
        db.scalar(
            select(func.count()).select_from(ImageGeneration).where(
                ImageGeneration.user_id == user.id,
                ImageGeneration.created_at >= since,
            )
        ) or 0
    )
    sandboxes = int(
        db.scalar(
            select(func.count()).select_from(ProjectSandboxRun).where(
                ProjectSandboxRun.created_by == user.id,
                ProjectSandboxRun.created_at >= since,
            )
        ) or 0
    )

    projection = None
    if projected_mode:
        reserve_seconds = estimated_reserve_seconds(projected_mode, projected_verification_extra)
        reserve_cost = price_resource_ms(settings, "cpu", reserve_seconds * 1000)
        projection = {
            "mode": projected_mode,
            "reserve_seconds": reserve_seconds,
            "verification_extra_inference_budget": max(0, min(2, int(projected_verification_extra))),
            "estimated_cost_microunits": reserve_cost,
            "fits_remaining_compute": reserve_seconds <= remaining_seconds,
            "remaining_after_reserve_seconds": max(0, remaining_seconds - reserve_seconds),
        }

    resource_cost = measured.get("cost_microunits", {})
    return {
        "month": since.strftime("%Y-%m"),
        "plan": quota.plan,
        "compute": {
            "used_seconds": used_seconds,
            "limit_seconds": limit_seconds,
            "remaining_seconds": remaining_seconds,
            "utilization_percent": utilization,
            "warning": warning_for(utilization),
        },
        "projection": projection,
        "chat": chat,
        "resources": {
            "cpu_ms": int(measured.get("usage_ms", {}).get("cpu", 0)),
            "image_worker_ms": int(measured.get("usage_ms", {}).get("image_worker", 0)),
            "sandbox_ms": int(measured.get("usage_ms", {}).get("sandbox", 0)),
            "gpu_ms": int(measured.get("usage_ms", {}).get("gpu", 0)),
            "cost_microunits": resource_cost,
            "total_cost_microunits": int(measured.get("total_cost_microunits", 0)),
        },
        "activity": {
            "research_runs": research_runs,
            "image_generations": images,
            "sandbox_runs": sandboxes,
        },
    }


def record_compute_breakdown(
    db: Session,
    *,
    request_id: str,
    user_id: str,
    project_id: str | None,
    conversation_id: str | None,
    mode: str,
    primary_ms: int,
    critic_ms: int,
    repair_ms: int,
    total_inference_ms: int,
    success: bool,
    verification_extra_inferences: int,
    metadata: dict[str, Any] | None = None,
) -> ComputeBreakdownEvent:
    primary_ms = max(0, int(primary_ms))
    critic_ms = max(0, int(critic_ms))
    repair_ms = max(0, int(repair_ms))
    total_inference_ms = max(0, int(total_inference_ms))
    accounted = primary_ms + critic_ms + repair_ms
    if accounted > total_inference_ms:
        # Independent timers can be a few ms wider than the outer timer. Keep the
        # canonical total authoritative while preserving the component ratios.
        overflow = accounted - total_inference_ms
        primary_ms = max(0, primary_ms - overflow)
    wasted_ms = 0 if success else total_inference_ms
    row = ComputeBreakdownEvent(
        request_id=request_id,
        user_id=user_id,
        project_id=project_id,
        conversation_id=conversation_id,
        mode=mode,
        primary_ms=primary_ms,
        critic_ms=critic_ms,
        repair_ms=repair_ms,
        total_inference_ms=total_inference_ms,
        wasted_ms=wasted_ms,
        success=bool(success),
        verification_extra_inferences=max(0, int(verification_extra_inferences)),
        metadata_json=dict(metadata or {}),
    )
    db.add(row)
    return row
