from __future__ import annotations

import json
from datetime import datetime, timezone
from math import ceil
from pathlib import Path
from typing import Any

CONTEXT_TIERS = (8192, 12288, 16384)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _parse_time(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return _aware(parsed)


def read_capacity_report(settings, *, now: datetime | None = None) -> dict[str, Any]:
    now = _aware(now) or _now()
    path = Path(getattr(settings, "capacity_report_path", "./backups/capacity-latest.json")).expanduser().resolve()
    try:
        payload = json.loads(path.read_text("utf-8"))
    except FileNotFoundError:
        return {"status": "missing", "path": str(path), "reasons": ["report_missing"]}
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError):
        return {"status": "invalid", "path": str(path), "reasons": ["report_invalid"]}
    if not isinstance(payload, dict):
        return {"status": "invalid", "path": str(path), "reasons": ["report_invalid"]}

    reasons: list[str] = []
    if payload.get("status") != "passed":
        reasons.append("calibration_failed")
    recommendation = payload.get("recommendation") if isinstance(payload.get("recommendation"), dict) else {}
    context = int(recommendation.get("deep_context_tokens") or 0)
    if context not in CONTEXT_TIERS:
        reasons.append("invalid_context_recommendation")
    if int(recommendation.get("max_concurrent_generations") or 0) < 1:
        reasons.append("invalid_concurrency_recommendation")
    if int(recommendation.get("max_queue_size") or 0) < 1:
        reasons.append("invalid_queue_recommendation")

    finished = _parse_time(payload.get("finished_at"))
    if finished is None:
        reasons.append("missing_finished_at")
        age_hours = None
    else:
        age_hours = max(0.0, (now - finished).total_seconds() / 3600.0)
        if age_hours > float(getattr(settings, "capacity_report_max_age_hours", 168.0)):
            reasons.append("stale")

    result = dict(payload)
    result.update({"path": str(path), "status": "passed" if not reasons else "degraded", "reasons": reasons, "age_hours": None if age_hours is None else round(age_hours, 2)})
    return result


def _clamp(value: int, minimum: int, maximum: int) -> int:
    return max(minimum, min(maximum, int(value)))


def build_launch_calibration(beta_snapshot: dict[str, Any], capacity_report: dict[str, Any], settings) -> dict[str, Any]:
    readiness = dict(beta_snapshot.get("readiness") or {})
    metrics = dict(beta_snapshot.get("metrics") or {})
    blockers: list[str] = []
    warnings: list[str] = []

    active = int(beta_snapshot.get("active_participant_count") or 0)
    paused = int(beta_snapshot.get("paused_participant_count") or 0)
    participants = active + paused if (active or paused or "active_participant_count" in beta_snapshot) else int(beta_snapshot.get("enrolled_count") or 0)
    tasks = int(beta_snapshot.get("task_count") or 0)
    requests = int(beta_snapshot.get("request_count") or 0)
    if participants < int(getattr(settings, "beta_min_participants", 50)):
        blockers.append("beta_participants_below_minimum")
    if participants > int(getattr(settings, "beta_max_participants", 100)):
        warnings.append("beta_participants_above_target_range")
    if tasks < int(getattr(settings, "beta_min_tasks", 500)):
        blockers.append("beta_tasks_below_minimum")
    if capacity_report.get("status") != "passed":
        blockers.append("target_node_capacity_not_current")

    request_success = float(metrics.get("request_success_rate") or 0.0)
    if requests and request_success < float(getattr(settings, "beta_min_request_success_rate", 0.97)):
        blockers.append("request_success_rate_below_guardrail")
    frustration_rate = float(metrics.get("frustration_per_request") or 0.0)
    if requests and frustration_rate > float(getattr(settings, "beta_max_frustration_per_request", 0.05)):
        blockers.append("frustration_rate_above_guardrail")
    p95_queue = int(beta_snapshot.get("p95_queue_ms") or 0)
    if requests and p95_queue > int(getattr(settings, "beta_max_p95_queue_ms", 5000)):
        warnings.append("beta_queue_p95_above_target")

    recommendation = dict(capacity_report.get("recommendation") or {})
    measured_monthly_minutes = float(beta_snapshot.get("compute_minutes_per_active_user") or 0.0)
    if readiness.get("capacity_recalibration_ready") and measured_monthly_minutes > 0:
        headroom = max(1.0, float(getattr(settings, "capacity_compute_headroom_ratio", 1.25)))
        recommended_compute_seconds = _clamp(
            ceil(measured_monthly_minutes * 60.0 * headroom),
            int(getattr(settings, "capacity_min_monthly_compute_seconds", 300)),
            int(getattr(settings, "capacity_max_monthly_compute_seconds", 14400)),
        )
    else:
        recommended_compute_seconds = int(getattr(settings, "default_monthly_compute_seconds", 600))

    recommended_queue = int(recommendation.get("max_queue_size") or getattr(settings, "max_queue_size", 64))
    if p95_queue > int(getattr(settings, "beta_max_p95_queue_ms", 5000)):
        recommended_queue = min(recommended_queue, 8)

    plan = {
        "max_context_tokens": min(8192, int(recommendation.get("deep_context_tokens") or 8192)),
        "deep_context_tokens": int(recommendation.get("deep_context_tokens") or getattr(settings, "deep_context_tokens", 16384)),
        "max_concurrent_generations": int(recommendation.get("max_concurrent_generations") or 1),
        "max_queue_size": max(1, recommended_queue),
        "inference_queue_timeout_seconds": float(recommendation.get("inference_queue_timeout_seconds") or getattr(settings, "inference_queue_timeout_seconds", 120.0)),
        "default_monthly_compute_seconds": recommended_compute_seconds,
    }
    beta_only_blockers = {"beta_participants_below_minimum", "beta_tasks_below_minimum"}
    if not blockers:
        status = "ready"
    elif set(blockers).issubset(beta_only_blockers):
        status = "collecting_data"
    else:
        status = "blocked"

    return {
        "status": status,
        "blockers": blockers,
        "warnings": warnings,
        "plan": plan,
        "signals": {
            "participants": participants,
            "tasks": tasks,
            "requests": requests,
            "d1_retention": metrics.get("d1_retention", 0),
            "d7_retention": metrics.get("d7_retention", 0),
            "d30_retention": metrics.get("d30_retention", 0),
            "frustration_per_request": frustration_rate,
            "request_success_rate": request_success,
            "verified_request_success_per_cpu_minute": metrics.get("verified_request_success_per_cpu_minute", 0),
            "completed_task_success_per_cpu_minute": metrics.get("completed_task_success_per_cpu_minute", metrics.get("verified_task_success_per_cpu_minute", 0)),
            "p95_duration_ms": beta_snapshot.get("p95_duration_ms", 0),
            "p95_queue_ms": p95_queue,
        },
        "data_sufficiency": {
            "participant_target_met": participants >= int(getattr(settings, "beta_min_participants", 50)),
            "task_target_met": tasks >= int(getattr(settings, "beta_min_tasks", 500)),
            "capacity_recalibration_ready": bool(readiness.get("capacity_recalibration_ready")),
        },
        "retention_policy": "measured_signal_only_no_invented_release_threshold",
    }
