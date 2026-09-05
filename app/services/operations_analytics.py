from __future__ import annotations

from datetime import datetime, timedelta, timezone
from math import ceil

from sqlalchemy import func, inspect, select, text
from sqlalchemy.orm import Session

from app.models import EngineeringExecution, EngineeringRun, FrustrationEvent, ImageBlob, ImageFeedback, ImageGeneration, ImageQAEvent, UsageEvent


def _now():
    return datetime.now(timezone.utc)


def _pct(values: list[int], p: float) -> int:
    if not values: return 0
    values = sorted(int(x) for x in values)
    return values[max(0, min(len(values)-1, ceil(len(values)*p)-1))]


def _host_metrics() -> dict:
    from app.services.performance import host_metrics
    return host_metrics()


def _optional_scalar(db: Session, sql: str, params: dict | None = None, default=0):
    try: return db.execute(text(sql), params or {}).scalar_one_or_none() or default
    except Exception:
        db.rollback(); return default


def operations_summary(db: Session, *, window_hours: int = 24, monthly_server_cost_rub: float = 4000.0) -> dict:
    since = _now() - timedelta(hours=window_hours)
    usage = list(db.scalars(select(UsageEvent).where(UsageEvent.created_at >= since)).all())
    success = [x for x in usage if x.success]
    users = {x.user_id for x in usage}
    inference_ms = sum(x.inference_ms for x in usage)
    durations = [x.duration_ms for x in usage]; queues = [x.queue_ms for x in usage]
    raw_chars = sum(x.raw_chars for x in usage); compiled_chars = sum(x.compiled_chars for x in usage)
    frustration = int(db.scalar(select(func.count()).select_from(FrustrationEvent).where(FrustrationEvent.created_at >= since)) or 0)

    image_rows = list(db.scalars(select(ImageGeneration).where(ImageGeneration.created_at >= since)).all())
    image_ready = [x for x in image_rows if x.status == "ready"]
    image_failed = [x for x in image_rows if x.status in {"failed","error"}]
    image_latency = [int((x.finished_at-x.created_at).total_seconds()*1000) for x in image_rows if x.finished_at and x.created_at]
    feedback = list(db.scalars(select(ImageFeedback).where(ImageFeedback.created_at >= since)).all())
    avg_rating = round(sum(x.rating for x in feedback)/len(feedback),2) if feedback else None
    qa_total = int(db.scalar(select(func.count()).select_from(ImageQAEvent).where(ImageQAEvent.created_at >= since)) or 0)
    qa_failed = int(db.scalar(select(func.count()).select_from(ImageQAEvent).where(ImageQAEvent.created_at >= since, ImageQAEvent.status.in_(["failed","error"]))) or 0)
    storage_bytes = int(db.scalar(select(func.coalesce(func.sum(ImageBlob.size_bytes),0))) or 0)

    executions = list(db.scalars(select(EngineeringExecution).where(EngineeringExecution.created_at >= since)).all())
    runs = list(db.scalars(select(EngineeringRun).where(EngineeringRun.created_at >= since)).all())
    verified = [x for x in executions if x.status == "verified"]
    blocked = [x for x in executions if x.status in {"blocked","rolled_back"}]
    active_runs = [x for x in runs if x.status == "running"]

    tables = set(inspect(db.get_bind()).get_table_names())
    revenue_minor = 0; resource_cost_microunits = 0
    if "payment_records" in tables:
        revenue_minor = int(_optional_scalar(db,"SELECT COALESCE(SUM(amount_minor),0) FROM payment_records WHERE created_at >= :since AND status IN ('received','paid','reconciled','succeeded')",{"since":since}))
    if "resource_expense_events" in tables:
        resource_cost_microunits = int(_optional_scalar(db,"SELECT COALESCE(SUM(cost_microunits),0) FROM resource_expense_events WHERE created_at >= :since",{"since":since}))
    allocated_server_cost = round(monthly_server_cost_rub*(window_hours/(24*30)),2)
    revenue_rub = round(revenue_minor/100,2)

    return {
      "window_hours":window_hours,
      "economics":{"recognized_revenue_rub":revenue_rub,"allocated_server_cost_rub":allocated_server_cost,"gross_after_server_rub":round(revenue_rub-allocated_server_cost,2),"resource_cost_microunits":resource_cost_microunits,"note":"Gross-after-server excludes taxes and other external costs; resource microunits are shown separately."},
      "traffic":{"requests":len(usage),"successful":len(success),"success_rate":round(len(success)/max(1,len(usage)),4),"active_users":len(users),"p95_duration_ms":_pct(durations,.95),"p95_queue_ms":_pct(queues,.95),"inference_minutes":round(inference_ms/60000,3),"cpu_seconds_per_success":round((inference_ms/1000)/max(1,len(success)),3),"context_efficiency_ratio":round(compiled_chars/max(1,raw_chars),4),"frustration_rate":round(frustration/max(1,len(usage)),4)},
      "resources":{**_host_metrics(),"image_storage_mb":round(storage_bytes/1024/1024,2)},
      "images":{"generated":len(image_rows),"ready":len(image_ready),"failed":len(image_failed),"success_rate":round(len(image_ready)/max(1,len(image_rows)),4),"p95_end_to_end_ms":_pct(image_latency,.95),"qa_events":qa_total,"qa_failures":qa_failed,"qa_failure_rate":round(qa_failed/max(1,qa_total),4),"feedback_count":len(feedback),"avg_user_rating":avg_rating},
      "agents":{"engineering_runs":len(runs),"active_runs":len(active_runs),"executions":len(executions),"verified":len(verified),"blocked_or_rolled_back":len(blocked),"verified_rate":round(len(verified)/max(1,len(executions)),4),"failure_rate":round(len(blocked)/max(1,len(executions)),4)}
    }
