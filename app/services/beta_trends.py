from __future__ import annotations

from typing import Any

from app.services.beta import snapshot_dict


def _metric(snapshot: dict[str, Any], key: str) -> float:
    metrics = snapshot.get("metrics") if isinstance(snapshot.get("metrics"), dict) else {}
    try:
        return float(metrics.get(key) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _number(snapshot: dict[str, Any], key: str) -> float:
    try:
        return float(snapshot.get(key) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def compare_snapshots(current: dict[str, Any], previous: dict[str, Any], settings) -> dict[str, Any]:
    success_now = _metric(current, "request_success_rate")
    success_prev = _metric(previous, "request_success_rate")
    frustration_now = _metric(current, "frustration_per_request")
    frustration_prev = _metric(previous, "frustration_per_request")
    quality_now = _metric(current, "quality_supported_request_rate")
    quality_prev = _metric(previous, "quality_supported_request_rate")
    efficiency_now = _metric(current, "verified_request_success_per_cpu_minute")
    efficiency_prev = _metric(previous, "verified_request_success_per_cpu_minute")
    queue_now = _number(current, "p95_queue_ms")
    queue_prev = _number(previous, "p95_queue_ms")
    duration_now = _number(current, "p95_duration_ms")
    duration_prev = _number(previous, "p95_duration_ms")

    deltas = {
        "request_success_rate": round(success_now - success_prev, 4),
        "frustration_per_request": round(frustration_now - frustration_prev, 4),
        "quality_supported_request_rate": round(quality_now - quality_prev, 4),
        "verified_request_success_per_cpu_minute": round(efficiency_now - efficiency_prev, 6),
        "p95_queue_ms": int(queue_now - queue_prev),
        "p95_duration_ms": int(duration_now - duration_prev),
        "d1_retention": round(_metric(current, "d1_retention") - _metric(previous, "d1_retention"), 4),
        "d7_retention": round(_metric(current, "d7_retention") - _metric(previous, "d7_retention"), 4),
        "d30_retention": round(_metric(current, "d30_retention") - _metric(previous, "d30_retention"), 4),
    }

    anomalies: list[str] = []
    warnings: list[str] = []
    if success_prev > 0 and success_now < success_prev - float(getattr(settings, "beta_trend_max_success_drop", 0.02)):
        anomalies.append("request_success_rate_regressed")
    if frustration_now > frustration_prev + float(getattr(settings, "beta_trend_max_frustration_increase", 0.02)):
        anomalies.append("frustration_rate_regressed")
    if quality_prev > 0 and quality_now < quality_prev - float(getattr(settings, "beta_trend_max_quality_drop", 0.05)):
        anomalies.append("quality_supported_rate_regressed")

    queue_ratio = float(getattr(settings, "beta_wave_max_queue_regression_ratio", 1.5))
    if queue_prev > 0 and queue_now > queue_prev * queue_ratio:
        anomalies.append("p95_queue_regressed")
    duration_ratio = float(getattr(settings, "beta_wave_max_duration_regression_ratio", 1.5))
    if duration_prev > 0 and duration_now > duration_prev * duration_ratio:
        anomalies.append("p95_duration_regressed")
    efficiency_ratio = float(getattr(settings, "beta_wave_min_cpu_efficiency_ratio", 0.70))
    if efficiency_prev > 0 and efficiency_now > 0 and efficiency_now < efficiency_prev * efficiency_ratio:
        anomalies.append("verified_cpu_efficiency_regressed")

    if _number(current, "request_count") < int(getattr(settings, "beta_wave_min_observation_requests", 50)):
        warnings.append("current_snapshot_has_low_request_count")

    return {
        "status": "regressed" if anomalies else "stable",
        "anomalies": sorted(set(anomalies)),
        "warnings": warnings,
        "deltas": deltas,
        "current_snapshot_id": current.get("id"),
        "previous_snapshot_id": previous.get("id"),
        "current_created_at": current.get("created_at"),
        "previous_created_at": previous.get("created_at"),
    }


def build_trend(rows, settings) -> dict[str, Any]:
    snapshots = [snapshot_dict(row) for row in rows]
    snapshots.sort(key=lambda item: str(item.get("created_at") or ""))
    comparisons = [
        compare_snapshots(snapshots[index], snapshots[index - 1], settings)
        for index in range(1, len(snapshots))
    ]
    latest = comparisons[-1] if comparisons else None
    return {
        "status": latest["status"] if latest else "insufficient_history",
        "latest": latest,
        "comparisons": comparisons,
        "snapshot_count": len(snapshots),
        "retention_policy": "D1/D7/D30 are reported as measured product signals, not hard release thresholds",
    }
