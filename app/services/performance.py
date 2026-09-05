from __future__ import annotations

from datetime import datetime, timedelta, timezone
from math import ceil
import os

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import AnswerAudit, FrustrationEvent, PerformanceSnapshot, UsageEvent


def percentile(values: list[int], p: float) -> int:
    if not values:
        return 0
    ordered = sorted(int(v) for v in values)
    idx = max(0, min(len(ordered) - 1, ceil(p * len(ordered)) - 1))
    return ordered[idx]



def host_metrics() -> dict:
    out: dict = {}
    try:
        with open("/proc/meminfo", "r", encoding="utf-8") as fh:
            mem = {}
            for line in fh:
                key, value = line.split(":", 1)
                mem[key] = int(value.strip().split()[0])
        out["memory_total_mb"] = round(mem.get("MemTotal", 0) / 1024, 2)
        out["memory_available_mb"] = round(mem.get("MemAvailable", 0) / 1024, 2)
    except (OSError, ValueError):
        pass
    try:
        page_size = os.sysconf("SC_PAGE_SIZE")
        with open("/proc/self/statm", "r", encoding="utf-8") as fh:
            resident_pages = int(fh.read().split()[1])
        out["process_rss_mb"] = round(resident_pages * page_size / 1024 / 1024, 2)
    except (OSError, ValueError, IndexError):
        pass
    try:
        one, five, fifteen = os.getloadavg()
        out.update({"load_1m": round(one, 3), "load_5m": round(five, 3), "load_15m": round(fifteen, 3)})
    except OSError:
        pass
    return out

def build_snapshot(db: Session, window_minutes: int = 60) -> PerformanceSnapshot:
    since = datetime.now(timezone.utc) - timedelta(minutes=window_minutes)
    rows = db.scalars(select(UsageEvent).where(UsageEvent.created_at >= since)).all()
    durations = [r.duration_ms for r in rows]
    queues = [r.queue_ms for r in rows]
    inference = [r.inference_ms for r in rows]
    successes = [r for r in rows if r.success]
    total_inference_ms = sum(r.inference_ms for r in rows)
    raw_chars = sum(r.raw_chars for r in rows)
    compiled_chars = sum(r.compiled_chars for r in rows)
    frustration_count = int(db.scalar(select(func.count()).select_from(FrustrationEvent).where(FrustrationEvent.created_at >= since)) or 0)
    quality_failure_count = int(db.scalar(select(func.count()).select_from(AnswerAudit).where(AnswerAudit.created_at >= since, AnswerAudit.status == "failed")) or 0)
    snapshot = PerformanceSnapshot(
        window_minutes=window_minutes,
        request_count=len(rows), success_count=len(successes),
        p50_duration_ms=percentile(durations, 0.50), p95_duration_ms=percentile(durations, 0.95), p99_duration_ms=percentile(durations, 0.99),
        p50_queue_ms=percentile(queues, 0.50), p95_queue_ms=percentile(queues, 0.95), p95_inference_ms=percentile(inference, 0.95),
        cpu_seconds_per_success=round((total_inference_ms / 1000) / max(1, len(successes)), 4),
        context_efficiency_ratio=round(compiled_chars / max(1, raw_chars), 4),
        frustration_count=frustration_count, quality_failure_count=quality_failure_count,
        metrics={"inference_ms_total": total_inference_ms, "raw_chars_total": raw_chars, "compiled_chars_total": compiled_chars,
                 "success_rate": round(len(successes) / max(1, len(rows)), 4),
                 "frustration_rate": round(frustration_count / max(1, len(rows)), 4),
                 "quality_failure_rate": round(quality_failure_count / max(1, len(rows)), 4),
                 "ttft_available": False, **host_metrics()},
    )
    db.add(snapshot)
    return snapshot


def evaluate_candidate(baseline: dict, candidate: dict) -> tuple[str, list[str]]:
    reasons: list[str] = []
    for key in ("quality_pass_rate", "frustration_rate", "p95_duration_ms", "cpu_seconds_per_success"):
        if key not in baseline or key not in candidate:
            reasons.append(f"missing metric: {key}")
    if reasons:
        return "rejected", reasons
    if float(candidate["quality_pass_rate"]) + 0.005 < float(baseline["quality_pass_rate"]):
        reasons.append("quality_pass_rate regressed")
    if float(candidate["frustration_rate"]) > float(baseline["frustration_rate"]) + 0.005:
        reasons.append("frustration_rate increased")
    duration_improved = float(candidate["p95_duration_ms"]) < float(baseline["p95_duration_ms"])
    cpu_improved = float(candidate["cpu_seconds_per_success"]) < float(baseline["cpu_seconds_per_success"])
    if not (duration_improved or cpu_improved):
        reasons.append("no measurable latency or CPU efficiency improvement")
    return ("accepted", []) if not reasons else ("rejected", reasons)
