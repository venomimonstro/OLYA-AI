from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from math import ceil
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    BetaSnapshot,
    CapacityPlan,
    CircuitBreakerEvent,
    MeasuredPlanCatalog,
    PublicRollout,
    ResourceExpenseEvent,
    SystemCheckpoint,
    UsageEvent,
)
from app.services.beta_trends import build_trend
from app.services.capacity import read_capacity_report

ROLLOUT_STAGES = (1, 5, 10, 25, 50, 100)
PLAN_ORDER = ("free", "x1", "pro", "max", "business")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def active_rollout(db: Session) -> PublicRollout | None:
    return db.scalar(
        select(PublicRollout)
        .where(PublicRollout.state == "active")
        .order_by(PublicRollout.version.desc())
        .limit(1)
    )


def active_measured_catalog(db: Session) -> MeasuredPlanCatalog | None:
    return db.scalar(
        select(MeasuredPlanCatalog)
        .where(MeasuredPlanCatalog.status == "active")
        .order_by(MeasuredPlanCatalog.version.desc())
        .limit(1)
    )


def rollout_dict(row: PublicRollout) -> dict[str, Any]:
    return {
        "id": row.id,
        "version": row.version,
        "state": row.state,
        "exposure_percent": row.exposure_percent,
        "baseline_metrics": row.baseline_metrics,
        "latest_metrics": row.latest_metrics,
        "guardrails": row.guardrails,
        "decision": row.decision,
        "previous_rollout_id": row.previous_rollout_id,
        "opened_at": row.opened_at,
        "paused_at": row.paused_at,
        "completed_at": row.completed_at,
        "rolled_back_at": row.rolled_back_at,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def catalog_dict(row: MeasuredPlanCatalog) -> dict[str, Any]:
    return {
        "id": row.id,
        "version": row.version,
        "status": row.status,
        "source": row.source,
        "catalog": row.catalog,
        "economics": row.economics,
        "guardrails": row.guardrails,
        "previous_catalog_id": row.previous_catalog_id,
        "approved_at": row.approved_at,
        "activated_at": row.activated_at,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def assignment_bucket(user_id: str, salt: str) -> int:
    digest = hashlib.sha256(f"{salt}:{user_id}".encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big") % 10_000


def rollout_allows_user(row: PublicRollout | None, user_id: str) -> bool:
    if row is None or row.state != "active" or row.exposure_percent <= 0:
        return False
    if row.exposure_percent >= 100:
        return True
    return assignment_bucket(user_id, row.assignment_salt) < row.exposure_percent * 100


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, int(value)))


def _normalized_channel_shares(settings) -> dict[str, float]:
    raw = {
        "fast": float(getattr(settings, "plan_share_fast", 0.20)),
        "work": float(getattr(settings, "plan_share_work", 0.35)),
        "deep": float(getattr(settings, "plan_share_deep", 0.25)),
        "api": float(getattr(settings, "plan_share_api", 0.10)),
        "image_worker": float(getattr(settings, "plan_share_image", 0.05)),
        "sandbox": float(getattr(settings, "plan_share_sandbox", 0.05)),
    }
    total = sum(max(0.0, value) for value in raw.values()) or 1.0
    return {key: round(max(0.0, value) / total, 6) for key, value in raw.items()}


def build_measured_plan_catalog(beta_metrics: dict[str, Any], capacity_report: dict[str, Any], settings) -> dict[str, Any]:
    readiness = dict(beta_metrics.get("readiness") or {})
    blockers: list[str] = []
    if not readiness.get("capacity_recalibration_ready"):
        blockers.append("beta_sample_not_sufficient")
    if capacity_report.get("status") != "passed":
        blockers.append("target_node_capacity_not_current")

    measured_minutes = float(beta_metrics.get("compute_minutes_per_active_user") or 0.0)
    if measured_minutes <= 0:
        blockers.append("measured_compute_per_user_missing")

    base_seconds = ceil(measured_minutes * 60.0 * max(1.0, float(getattr(settings, "capacity_compute_headroom_ratio", 1.25))))
    base_seconds = _clamp(
        base_seconds or int(getattr(settings, "default_monthly_compute_seconds", 600)),
        int(getattr(settings, "capacity_min_monthly_compute_seconds", 300)),
        int(getattr(settings, "capacity_max_monthly_compute_seconds", 14400)),
    )
    ratios = {
        "free": float(getattr(settings, "plan_ratio_free", 0.25)),
        "x1": float(getattr(settings, "plan_ratio_x1", 1.0)),
        "pro": float(getattr(settings, "plan_ratio_pro", 2.0)),
        "max": float(getattr(settings, "plan_ratio_max", 4.0)),
        "business": float(getattr(settings, "plan_ratio_business", 8.0)),
    }
    recommendation = dict(capacity_report.get("recommendation") or {})
    node_concurrency = max(1, int(recommendation.get("max_concurrent_generations") or 1))
    cpu_rate = max(1, int(getattr(settings, "commerce_cpu_microunits_per_second", 1000)))
    shares = _normalized_channel_shares(settings)

    catalog: dict[str, dict[str, Any]] = {}
    for name in PLAN_ORDER:
        monthly_seconds = _clamp(
            ceil(base_seconds * max(0.05, ratios[name])),
            int(getattr(settings, "capacity_min_monthly_compute_seconds", 300)),
            int(getattr(settings, "capacity_max_monthly_compute_seconds", 14400)) * (4 if name == "business" else 1),
        )
        catalog[name] = {
            "name": name,
            "monthly_cpu_seconds": monthly_seconds,
            "resource_budget_microunits": monthly_seconds * cpu_rate,
            "max_concurrent_inference": node_concurrency,
            "max_concurrent_jobs": max(1, min(16, int(getattr(settings, "default_max_concurrent_jobs", 1)) * (1 if name == "free" else 2 if name in {"x1", "pro"} else 4))),
            "organization_enabled": name == "business",
            "channel_shares": shares,
            "source": "measured_beta_cpu_with_operator_plan_ratios",
        }

    metrics = dict(beta_metrics.get("metrics") or {})
    return {
        "status": "ready" if not blockers else "blocked",
        "blockers": blockers,
        "catalog": catalog,
        "economics": {
            "measured_compute_minutes_per_active_user": measured_minutes,
            "base_monthly_compute_seconds_with_headroom": base_seconds,
            "monthly_server_cost_rub": float(getattr(settings, "monthly_server_cost_rub", 0.0)),
            "verified_request_success_per_cpu_minute": metrics.get("verified_request_success_per_cpu_minute", 0),
            "completed_task_success_per_cpu_minute": metrics.get("completed_task_success_per_cpu_minute", metrics.get("verified_task_success_per_cpu_minute", 0)),
            "node_max_concurrent_generations": node_concurrency,
        },
        "policy": {
            "plan_ratios": ratios,
            "channel_shares": shares,
            "note": "Ratios are operator product policy; base capacity is measured from beta telemetry.",
        },
    }


def create_measured_catalog(db: Session, *, measured: dict[str, Any], actor_id: str | None) -> MeasuredPlanCatalog:
    if measured.get("status") != "ready" or measured.get("blockers"):
        raise ValueError("Measured plan catalog cannot be created before beta/capacity gates pass")
    current = active_measured_catalog(db)
    version = int(db.scalar(select(func.max(MeasuredPlanCatalog.version))) or 0) + 1
    row = MeasuredPlanCatalog(
        version=version,
        status="draft",
        catalog=dict(measured.get("catalog") or {}),
        economics=dict(measured.get("economics") or {}),
        guardrails={"blockers": [], "policy": measured.get("policy") or {}},
        previous_catalog_id=current.id if current else None,
        created_by=actor_id,
    )
    db.add(row)
    db.flush()
    return row


def approve_catalog(row: MeasuredPlanCatalog, actor_id: str | None) -> None:
    if row.status != "draft":
        raise ValueError("Only a draft measured catalog can be approved")
    if not row.catalog or (row.guardrails or {}).get("blockers"):
        raise ValueError("Measured catalog has unresolved blockers")
    row.status = "approved"
    row.approved_by = actor_id
    row.approved_at = utcnow()


def activate_catalog(db: Session, row: MeasuredPlanCatalog) -> None:
    if row.status != "approved":
        raise ValueError("Measured catalog must be approved before activation")
    current = active_measured_catalog(db)
    if current is not None and current.id != row.id:
        current.status = "superseded"
        if not row.previous_catalog_id:
            row.previous_catalog_id = current.id
    row.status = "active"
    row.activated_at = utcnow()


def open_breakers(db: Session, scope: str = "public-launch") -> list[CircuitBreakerEvent]:
    return list(db.scalars(select(CircuitBreakerEvent).where(CircuitBreakerEvent.scope == scope, CircuitBreakerEvent.status == "open")).all())


def trip_breaker(db: Session, *, kind: str, reason: str, details: dict[str, Any] | None = None, scope: str = "public-launch", automatic: bool = True, actor_id: str | None = None) -> CircuitBreakerEvent:
    existing = db.scalar(select(CircuitBreakerEvent).where(CircuitBreakerEvent.scope == scope, CircuitBreakerEvent.kind == kind, CircuitBreakerEvent.status == "open"))
    if existing is not None:
        existing.reason = reason[:500]
        existing.details = details or {}
        return existing
    row = CircuitBreakerEvent(scope=scope, kind=kind, status="open", automatic=automatic, reason=reason[:500], details=details or {}, opened_by=actor_id)
    db.add(row)
    db.flush()
    return row


def resolve_breaker(row: CircuitBreakerEvent, actor_id: str | None) -> None:
    if row.status != "open":
        return
    row.status = "resolved"
    row.resolved_by = actor_id
    row.resolved_at = utcnow()


def _latest_trend(db: Session, settings, cohort: str = "closed-beta-1") -> dict[str, Any]:
    rows = list(db.scalars(select(BetaSnapshot).where(BetaSnapshot.cohort == cohort).order_by(BetaSnapshot.created_at.desc()).limit(14)).all())
    if len(rows) < 2:
        return {"status": "insufficient_data", "latest": None}
    window = rows[0].window_days
    rows = [row for row in rows if row.window_days == window]
    return build_trend(rows, settings) if len(rows) >= 2 else {"status": "insufficient_data", "latest": None}


def evaluate_public_launch(db: Session, settings, *, persist: bool = True) -> dict[str, Any]:
    blockers: list[str] = []
    warnings: list[str] = []
    capacity = read_capacity_report(settings)
    if capacity.get("status") != "passed":
        blockers.append("capacity_calibration_not_current")

    capacity_plan = db.scalar(select(CapacityPlan).where(CapacityPlan.status == "active").order_by(CapacityPlan.version.desc()).limit(1))
    if capacity_plan is None or not bool(capacity_plan.runtime_applied):
        blockers.append("active_capacity_plan_not_applied")

    catalog = active_measured_catalog(db)
    if catalog is None:
        blockers.append("measured_plan_catalog_missing")

    critical = list(db.scalars(select(SystemCheckpoint).where(SystemCheckpoint.critical.is_(True), SystemCheckpoint.status != "stable")).all())
    if critical:
        blockers.append("critical_system_checkpoint_failed")

    trend = _latest_trend(db, settings)
    if trend.get("status") == "regressed":
        blockers.append("beta_trend_regressed")
    elif trend.get("status") == "insufficient_data":
        warnings.append("beta_trend_insufficient_data")

    now = utcnow()
    since = now - timedelta(hours=24)
    request_count = int(db.scalar(select(func.count(UsageEvent.id)).where(UsageEvent.created_at >= since)) or 0)
    failed_count = int(db.scalar(select(func.count(UsageEvent.id)).where(UsageEvent.created_at >= since, UsageEvent.success.is_(False))) or 0)
    failure_rate = failed_count / request_count if request_count else 0.0
    min_requests = max(1, int(getattr(settings, "public_launch_breaker_min_requests", 50)))
    if request_count >= min_requests and failure_rate > float(getattr(settings, "public_launch_max_failure_rate", 0.03)):
        blockers.append("global_failure_rate_breaker")
        if persist:
            trip_breaker(db, kind="failure_rate", reason="24h request failure rate exceeded public-launch guardrail", details={"requests": request_count, "failures": failed_count, "failure_rate": round(failure_rate, 6)})

    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    spend = int(db.scalar(select(func.coalesce(func.sum(ResourceExpenseEvent.cost_microunits), 0)).where(ResourceExpenseEvent.created_at >= month_start)) or 0)
    budget = max(0, int(getattr(settings, "public_launch_global_budget_microunits", 0)))
    if budget > 0 and spend >= budget:
        blockers.append("global_resource_budget_breaker")
        if persist:
            trip_breaker(db, kind="resource_budget", reason="Global measured resource budget exhausted", details={"spent_microunits": spend, "budget_microunits": budget})

    breaker_rows = open_breakers(db)
    if breaker_rows:
        blockers.append("open_circuit_breaker")

    return {
        "status": "stable" if not blockers else "blocked",
        "blockers": sorted(set(blockers)),
        "warnings": sorted(set(warnings)),
        "capacity": capacity,
        "active_capacity_plan_id": capacity_plan.id if capacity_plan else None,
        "active_measured_catalog_id": catalog.id if catalog else None,
        "trend": trend,
        "request_24h": {"count": request_count, "failed": failed_count, "failure_rate": round(failure_rate, 6)},
        "resource_month": {"spent_microunits": spend, "budget_microunits": budget},
        "open_breakers": [{"id": row.id, "kind": row.kind, "reason": row.reason, "opened_at": row.opened_at} for row in breaker_rows],
        "evaluated_at": now.isoformat(),
    }


def create_rollout(db: Session, *, baseline: dict[str, Any], actor_id: str | None) -> PublicRollout:
    current = active_rollout(db)
    if current is not None:
        raise ValueError("An active public rollout already exists")
    version = int(db.scalar(select(func.max(PublicRollout.version))) or 0) + 1
    row = PublicRollout(version=version, state="planned", exposure_percent=0, baseline_metrics=baseline, latest_metrics=baseline, guardrails={"stages": list(ROLLOUT_STAGES)}, decision={"status": "planned"}, created_by=actor_id)
    db.add(row)
    db.flush()
    return row


def _next_rollout_version(db: Session) -> int:
    return int(db.scalar(select(func.max(PublicRollout.version))) or 0) + 1


def advance_rollout(db: Session, current: PublicRollout, *, exposure_percent: int, evaluation: dict[str, Any], actor_id: str | None) -> PublicRollout:
    if exposure_percent not in ROLLOUT_STAGES:
        raise ValueError(f"Exposure must be one of {ROLLOUT_STAGES}")
    if current.state not in {"planned", "active", "paused"}:
        raise ValueError("Rollout is not advanceable")
    if exposure_percent <= current.exposure_percent:
        raise ValueError("New rollout exposure must be greater than current exposure")
    if evaluation.get("status") != "stable":
        current.state = "paused"
        current.paused_at = utcnow()
        current.decision = {"status": "paused", "evaluation": evaluation}
        raise ValueError("Public rollout guardrails are not green")
    current.state = "superseded"
    row = PublicRollout(
        version=_next_rollout_version(db),
        state="active",
        exposure_percent=exposure_percent,
        assignment_salt=current.assignment_salt,
        baseline_metrics=current.baseline_metrics,
        latest_metrics=evaluation,
        guardrails=current.guardrails,
        decision={"status": "advanced", "from_percent": current.exposure_percent, "to_percent": exposure_percent, "evaluation": evaluation},
        previous_rollout_id=current.id,
        created_by=actor_id,
        approved_by=actor_id,
        opened_at=utcnow(),
        completed_at=utcnow() if exposure_percent == 100 else None,
    )
    db.add(row)
    db.flush()
    return row


def freeze_rollout(row: PublicRollout, *, evaluation: dict[str, Any], actor_id: str | None = None) -> None:
    if row.state != "active":
        return
    row.state = "paused"
    row.paused_at = utcnow()
    row.approved_by = actor_id or row.approved_by
    row.decision = {"status": "paused", "evaluation": evaluation}


def rollback_rollout(db: Session, current: PublicRollout, *, actor_id: str | None) -> PublicRollout:
    target = db.get(PublicRollout, current.previous_rollout_id) if current.previous_rollout_id else None
    target_percent = target.exposure_percent if target is not None else 0
    current.state = "rolled_back"
    current.rolled_back_at = utcnow()
    row = PublicRollout(
        version=_next_rollout_version(db),
        state="active" if target_percent > 0 else "planned",
        exposure_percent=target_percent,
        assignment_salt=current.assignment_salt,
        baseline_metrics=current.baseline_metrics,
        latest_metrics=current.latest_metrics,
        guardrails=current.guardrails,
        decision={"status": "rollback", "from_percent": current.exposure_percent, "to_percent": target_percent, "rolled_back_version": current.version},
        previous_rollout_id=current.id,
        created_by=actor_id,
        approved_by=actor_id,
        opened_at=utcnow() if target_percent > 0 else None,
    )
    db.add(row)
    db.flush()
    return row
