from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import BetaWave, CapacityPlan


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _integer(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _metric(payload: dict[str, Any], key: str, fallback: str | None = None) -> Any:
    metrics = payload.get("metrics") if isinstance(payload.get("metrics"), dict) else {}
    if key in metrics:
        return metrics[key]
    if key in payload:
        return payload[key]
    if fallback and fallback in metrics:
        return metrics[fallback]
    if fallback and fallback in payload:
        return payload[fallback]
    return 0


def signal_snapshot(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalize either a full beta snapshot or an already compact wave baseline."""
    return {
        "request_count": _integer(payload.get("request_count")),
        "success_count": _integer(payload.get("success_count")),
        "frustration_count": _integer(payload.get("frustration_count")),
        "task_count": _integer(payload.get("task_count")),
        "completed_task_count": _integer(payload.get("completed_task_count")),
        "compute_minutes_total": _number(payload.get("compute_minutes_total")),
        "p95_duration_ms": _integer(payload.get("p95_duration_ms")),
        "p95_queue_ms": _integer(payload.get("p95_queue_ms")),
        "request_success_rate": _number(_metric(payload, "request_success_rate")),
        "frustration_per_request": _number(_metric(payload, "frustration_per_request")),
        "verified_request_success_per_cpu_minute": _number(_metric(payload, "verified_request_success_per_cpu_minute")),
        "completed_task_success_per_cpu_minute": _number(
            _metric(payload, "completed_task_success_per_cpu_minute", "verified_task_success_per_cpu_minute")
        ),
        "d1_retention": _number(_metric(payload, "d1_retention")),
        "d7_retention": _number(_metric(payload, "d7_retention")),
        "d30_retention": _number(_metric(payload, "d30_retention")),
        "measured_at": str(payload.get("measured_at") or utcnow().isoformat()),
    }


def compare_wave_signals(current_payload: dict[str, Any], baseline_payload: dict[str, Any], settings) -> dict[str, Any]:
    current = signal_snapshot(current_payload)
    baseline = signal_snapshot(baseline_payload)
    delta_requests = max(0, current["request_count"] - baseline["request_count"])
    delta_successes = max(0, current["success_count"] - baseline["success_count"])
    delta_frustration = max(0, current["frustration_count"] - baseline["frustration_count"])
    min_requests = max(1, int(getattr(settings, "beta_wave_min_observation_requests", 50)))
    enough_observation = delta_requests >= min_requests
    wave_success_rate = delta_successes / delta_requests if delta_requests else 0.0
    wave_frustration_rate = delta_frustration / delta_requests if delta_requests else 0.0

    regressions: list[str] = []
    warnings: list[str] = []
    if enough_observation:
        if wave_success_rate < float(getattr(settings, "beta_min_request_success_rate", 0.97)):
            regressions.append("wave_request_success_below_guardrail")
        if wave_frustration_rate > float(getattr(settings, "beta_max_frustration_per_request", 0.05)):
            regressions.append("wave_frustration_above_guardrail")
        base_queue = baseline["p95_queue_ms"]
        if base_queue > 0 and current["p95_queue_ms"] > base_queue * float(getattr(settings, "beta_wave_max_queue_regression_ratio", 1.5)):
            regressions.append("p95_queue_regressed")
        base_duration = baseline["p95_duration_ms"]
        if base_duration > 0 and current["p95_duration_ms"] > base_duration * float(getattr(settings, "beta_wave_max_duration_regression_ratio", 1.5)):
            regressions.append("p95_duration_regressed")
        base_efficiency = baseline["verified_request_success_per_cpu_minute"]
        current_efficiency = current["verified_request_success_per_cpu_minute"]
        if base_efficiency > 0 and current_efficiency > 0 and current_efficiency < base_efficiency * float(getattr(settings, "beta_wave_min_cpu_efficiency_ratio", 0.70)):
            regressions.append("verified_cpu_efficiency_regressed")
    else:
        warnings.append("observation_window_incomplete")

    if current["p95_queue_ms"] > int(getattr(settings, "beta_max_p95_queue_ms", 5000)):
        regressions.append("p95_queue_above_guardrail")

    return {
        "delta_requests": delta_requests,
        "delta_successes": delta_successes,
        "delta_frustration": delta_frustration,
        "wave_request_success_rate": round(wave_success_rate, 4) if delta_requests else None,
        "wave_frustration_per_request": round(wave_frustration_rate, 4) if delta_requests else None,
        "enough_observation": enough_observation,
        "minimum_observation_requests": min_requests,
        "regressions": sorted(set(regressions)),
        "warnings": warnings,
        "current": current,
        "baseline": baseline,
    }


def active_wave(db: Session, cohort: str) -> BetaWave | None:
    return db.scalar(
        select(BetaWave)
        .where(BetaWave.cohort == cohort, BetaWave.state.in_(["planned", "open", "observing", "paused"]))
        .order_by(BetaWave.wave_number.desc())
        .limit(1)
    )


def wave_dict(wave: BetaWave) -> dict[str, Any]:
    return {
        "id": wave.id,
        "cohort": wave.cohort,
        "wave_number": wave.wave_number,
        "state": wave.state,
        "target_participants": wave.target_participants,
        "admitted_count": wave.admitted_count,
        "compute_budget_seconds": wave.compute_budget_seconds,
        "admission_paused": wave.admission_paused,
        "pause_reason": wave.pause_reason,
        "baseline_metrics": wave.baseline_metrics,
        "latest_metrics": wave.latest_metrics,
        "decision": wave.decision,
        "opened_at": wave.opened_at,
        "observing_at": wave.observing_at,
        "closed_at": wave.closed_at,
        "created_at": wave.created_at,
        "updated_at": wave.updated_at,
    }


def create_wave(
    db: Session,
    *,
    cohort: str,
    target_participants: int,
    baseline: dict[str, Any],
    actor_id: str | None,
    compute_budget_seconds: int = 0,
) -> BetaWave:
    existing = active_wave(db, cohort)
    if existing is not None:
        raise ValueError(f"Wave {existing.wave_number} is still {existing.state}")
    target = max(1, min(100, int(target_participants)))
    if compute_budget_seconds <= 0:
        from app.core.config import get_settings

        compute_budget_seconds = target * int(get_settings().default_monthly_compute_seconds)
    number = int(db.scalar(select(func.max(BetaWave.wave_number)).where(BetaWave.cohort == cohort)) or 0) + 1
    baseline_compact = signal_snapshot(baseline)
    wave = BetaWave(
        cohort=cohort,
        wave_number=number,
        state="planned",
        target_participants=target,
        compute_budget_seconds=max(60, int(compute_budget_seconds)),
        baseline_metrics=baseline_compact,
        latest_metrics=baseline_compact,
        decision={"status": "planned", "admission_allowed": False, "reasons": ["wave_not_open"]},
        created_by=actor_id,
    )
    db.add(wave)
    db.flush()
    return wave


def evaluate_wave(
    wave: BetaWave | None,
    *,
    current: dict[str, Any],
    capacity_report: dict[str, Any],
    settings,
    persist: bool = False,
) -> dict[str, Any]:
    comparison = compare_wave_signals(current, wave.baseline_metrics if wave else current, settings)
    compute_spent_seconds = max(
        0,
        int(round((comparison["current"]["compute_minutes_total"] - comparison["baseline"]["compute_minutes_total"]) * 60)),
    )
    comparison["compute_spent_seconds"] = compute_spent_seconds
    comparison["compute_budget_seconds"] = int(wave.compute_budget_seconds or 0) if wave is not None else 0

    reasons: list[str] = []
    warnings = list(comparison["warnings"])
    if wave is None:
        reasons.append("no_active_wave")
    else:
        if wave.state == "planned":
            reasons.append("wave_not_open")
        elif wave.state == "paused":
            reasons.append("wave_paused")
        elif wave.state == "observing":
            reasons.append("wave_target_reached_observing")
        elif wave.state != "open":
            reasons.append("wave_not_open")
        if wave.admission_paused and "wave_paused" not in reasons and "wave_target_reached_observing" not in reasons:
            reasons.append("admission_paused")
        if wave.admitted_count >= wave.target_participants:
            reasons.append("wave_target_reached")
        if wave.compute_budget_seconds > 0 and compute_spent_seconds >= wave.compute_budget_seconds:
            reasons.append("wave_compute_budget_exhausted")

    if capacity_report.get("status") != "passed":
        reasons.append("target_node_capacity_not_current")
    reasons.extend(comparison["regressions"])
    admission_allowed = not reasons and wave is not None and wave.state == "open"

    if wave is not None and wave.state == "observing" and comparison["enough_observation"] and not comparison["regressions"] and capacity_report.get("status") == "passed":
        status = "ready_to_close"
    elif admission_allowed:
        status = "admit"
    elif "wave_compute_budget_exhausted" in reasons:
        status = "paused_for_budget"
    elif comparison["regressions"]:
        status = "paused_for_regression"
    elif wave is not None and wave.state == "observing":
        status = "observing"
    else:
        status = "paused"

    decision = {
        "status": status,
        "admission_allowed": admission_allowed,
        "reasons": sorted(set(reasons)),
        "warnings": sorted(set(warnings)),
        "comparison": comparison,
        "capacity_status": capacity_report.get("status"),
        "evaluated_at": utcnow().isoformat(),
    }
    if wave is not None and persist:
        wave.latest_metrics = signal_snapshot(current)
        wave.decision = decision
        pause_reasons = list(comparison["regressions"])
        if "wave_compute_budget_exhausted" in reasons:
            pause_reasons.append("wave_compute_budget_exhausted")
        if wave.state == "open" and pause_reasons:
            wave.state = "paused"
            wave.admission_paused = True
            wave.pause_reason = ",".join(sorted(set(pause_reasons)))[:500]
        if wave.state == "open" and wave.admitted_count >= wave.target_participants:
            wave.state = "observing"
            wave.admission_paused = True
            wave.pause_reason = "wave_target_reached"
            wave.observing_at = wave.observing_at or utcnow()
    return decision


def record_wave_admission(wave: BetaWave) -> None:
    wave.admitted_count = int(wave.admitted_count or 0) + 1
    if wave.admitted_count >= wave.target_participants:
        wave.state = "observing"
        wave.admission_paused = True
        wave.pause_reason = "wave_target_reached"
        wave.observing_at = wave.observing_at or utcnow()
        wave.decision = {
            **dict(wave.decision or {}),
            "status": "observing",
            "admission_allowed": False,
            "reasons": ["wave_target_reached_observing"],
            "evaluated_at": utcnow().isoformat(),
        }


def plan_dict(plan: CapacityPlan) -> dict[str, Any]:
    return {
        "id": plan.id,
        "version": plan.version,
        "status": plan.status,
        "source": plan.source,
        "plan": plan.plan,
        "signals": plan.signals,
        "guardrails": plan.guardrails,
        "comparison": plan.comparison,
        "previous_plan_id": plan.previous_plan_id,
        "rollback_of_id": plan.rollback_of_id,
        "requires_restart": plan.requires_restart,
        "runtime_applied": plan.runtime_applied,
        "approved_at": plan.approved_at,
        "activated_at": plan.activated_at,
        "rolled_back_at": plan.rolled_back_at,
        "created_at": plan.created_at,
        "updated_at": plan.updated_at,
    }


def active_plan(db: Session) -> CapacityPlan | None:
    return db.scalar(select(CapacityPlan).where(CapacityPlan.status == "active").order_by(CapacityPlan.version.desc()).limit(1))


def compare_plans(candidate: dict[str, Any], previous: dict[str, Any] | None) -> dict[str, Any]:
    previous = dict(previous or {})
    keys = sorted(set(candidate) | set(previous))
    changes = {key: {"from": previous.get(key), "to": candidate.get(key)} for key in keys if previous.get(key) != candidate.get(key)}
    return {"changed": bool(changes), "changes": changes}


def propose_plan(
    db: Session,
    *,
    calibration: dict[str, Any],
    actor_id: str | None,
    source: str = "sprint37-calibration",
) -> CapacityPlan:
    candidate = dict(calibration.get("plan") or {})
    if not candidate:
        raise ValueError("Calibration has no capacity plan")
    current = active_plan(db)
    version = int(db.scalar(select(func.max(CapacityPlan.version))) or 0) + 1
    plan = CapacityPlan(
        version=version,
        status="draft",
        source=source,
        plan=candidate,
        signals=dict(calibration.get("signals") or {}),
        guardrails={
            "calibration_status": calibration.get("status"),
            "blockers": list(calibration.get("blockers") or []),
            "warnings": list(calibration.get("warnings") or []),
            "data_sufficiency": dict(calibration.get("data_sufficiency") or {}),
        },
        comparison=compare_plans(candidate, current.plan if current else None),
        previous_plan_id=current.id if current else None,
        created_by=actor_id,
        requires_restart=True,
        runtime_applied=False,
    )
    db.add(plan)
    db.flush()
    return plan


def approve_plan(plan: CapacityPlan, *, actor_id: str | None) -> None:
    if plan.status != "draft":
        raise ValueError("Only draft capacity plans can be approved")
    blockers = list((plan.guardrails or {}).get("blockers") or [])
    if blockers or (plan.guardrails or {}).get("calibration_status") != "ready":
        raise ValueError("Capacity plan cannot be approved while calibration is blocked or incomplete")
    plan.status = "approved"
    plan.approved_by = actor_id
    plan.approved_at = utcnow()


def _env_text(plan: CapacityPlan) -> str:
    mapping = {
        "max_context_tokens": "X1_MAX_CONTEXT_TOKENS",
        "deep_context_tokens": "X1_DEEP_CONTEXT_TOKENS",
        "max_concurrent_generations": "X1_MAX_CONCURRENT_GENERATIONS",
        "max_queue_size": "X1_MAX_QUEUE_SIZE",
        "inference_queue_timeout_seconds": "X1_INFERENCE_QUEUE_TIMEOUT_SECONDS",
        "default_monthly_compute_seconds": "X1_DEFAULT_MONTHLY_COMPUTE_SECONDS",
    }
    values = dict(plan.plan or {})
    lines = [f"# X1 capacity plan v{plan.version}; contains no secrets."]
    lines.extend(f"{env_name}={values[key]}" for key, env_name in mapping.items() if key in values)
    return "\n".join(lines) + "\n"


def export_plan_env(settings, plan: CapacityPlan) -> str:
    root = Path(getattr(settings, "backup_storage_path", "./backups")).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"capacity-plan-v{plan.version}.env"
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(_env_text(plan), "utf-8")
    os.replace(tmp, path)
    return str(path)


def apply_live_safe(app, plan: CapacityPlan) -> dict[str, Any]:
    settings = app.state.settings
    desired = dict(plan.plan or {})
    boot_deep = int(getattr(app.state, "capacity_boot_deep_context_tokens", settings.deep_context_tokens))
    boot_context = int(getattr(app.state, "capacity_boot_max_context_tokens", settings.max_context_tokens))
    boot_concurrency = int(getattr(app.state, "capacity_boot_max_concurrent_generations", settings.max_concurrent_generations))
    desired_deep = int(desired.get("deep_context_tokens", settings.deep_context_tokens))
    desired_context = int(desired.get("max_context_tokens", settings.max_context_tokens))
    desired_concurrency = int(desired.get("max_concurrent_generations", settings.max_concurrent_generations))
    restart_reasons: list[str] = []
    if desired_deep > boot_deep:
        restart_reasons.append("deep_context_above_boot_runtime")
    if desired_context > boot_context:
        restart_reasons.append("context_above_boot_runtime")
    if desired_concurrency != boot_concurrency:
        restart_reasons.append("concurrency_change_requires_governor_restart")

    artifact = export_plan_env(settings, plan)
    if restart_reasons:
        plan.requires_restart = True
        plan.runtime_applied = False
        return {"runtime_applied": False, "requires_restart": True, "restart_reasons": restart_reasons, "env_artifact": artifact}

    settings.max_context_tokens = desired_context
    settings.deep_context_tokens = desired_deep
    settings.max_queue_size = int(desired.get("max_queue_size", settings.max_queue_size))
    settings.inference_queue_timeout_seconds = float(desired.get("inference_queue_timeout_seconds", settings.inference_queue_timeout_seconds))
    settings.default_monthly_compute_seconds = int(desired.get("default_monthly_compute_seconds", settings.default_monthly_compute_seconds))
    app.state.context.max_chars = max(128, desired_deep * 6)
    app.state.governor.max_queue = settings.max_queue_size
    app.state.governor.wait_timeout_seconds = settings.inference_queue_timeout_seconds
    plan.requires_restart = False
    plan.runtime_applied = True
    return {"runtime_applied": True, "requires_restart": False, "restart_reasons": [], "env_artifact": artifact}


def activate_plan(db: Session, app, plan: CapacityPlan) -> dict[str, Any]:
    if plan.status != "approved":
        raise ValueError("Capacity plan must be approved before activation")
    current = active_plan(db)
    result = apply_live_safe(app, plan)
    if not result["runtime_applied"]:
        # Keep the previous known-good plan active until the operator applies the
        # generated env artifact and restarts. A pending restart must never make
        # database state claim that un-applied limits are active.
        return result
    if current is not None and current.id != plan.id:
        current.status = "superseded"
        if not plan.previous_plan_id:
            plan.previous_plan_id = current.id
    plan.status = "active"
    plan.activated_at = utcnow()
    return result


def rollback_plan(db: Session, app, *, actor_id: str | None, target_plan_id: str | None = None) -> tuple[CapacityPlan, dict[str, Any]]:
    current = active_plan(db)
    if current is None:
        raise ValueError("There is no active capacity plan to roll back")
    target_id = target_plan_id or current.previous_plan_id
    if not target_id:
        raise ValueError("Active capacity plan has no previous plan")
    if target_id == current.id:
        raise ValueError("Rollback target must differ from the active capacity plan")
    target = db.get(CapacityPlan, target_id)
    if target is None or target.status not in {"active", "superseded", "rolled_back"}:
        raise ValueError("Rollback target is not a previously activated capacity plan")

    version = int(db.scalar(select(func.max(CapacityPlan.version))) or 0) + 1
    rollback = CapacityPlan(
        version=version,
        status="approved",
        source="rollback",
        plan=dict(target.plan or {}),
        signals=dict(target.signals or {}),
        guardrails={"calibration_status": "rollback", "blockers": [], "warnings": ["emergency_or_operator_rollback"]},
        comparison=compare_plans(dict(target.plan or {}), dict(current.plan or {})),
        previous_plan_id=current.id,
        rollback_of_id=target.id,
        created_by=actor_id,
        approved_by=actor_id,
        approved_at=utcnow(),
        requires_restart=True,
        runtime_applied=False,
    )
    db.add(rollback)
    db.flush()
    result = activate_plan(db, app, rollback)
    if result["runtime_applied"]:
        current.status = "rolled_back"
        current.rolled_back_at = utcnow()
    return rollback, result
