from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import UsageEvent, User
from app.services.auth import get_current_user
from app.services.quota import compute_seconds_used, get_or_create_quota

router = APIRouter(prefix="/v1/usage", tags=["usage"])


class UsageSummary(BaseModel):
    events: int
    duration_ms: int
    inference_ms: int
    queue_ms: int
    raw_chars: int
    compiled_chars: int
    output_chars: int
    context_saved_percent: float
    monthly_compute_seconds_used: int
    monthly_compute_seconds_limit: int
    plan: str


@router.get("/summary", response_model=UsageSummary)
def usage_summary(
    request: Request,
    days: int = Query(default=30, ge=1, le=366),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> UsageSummary:
    since = datetime.now(timezone.utc) - timedelta(days=days)
    row = db.execute(
        select(
            func.count(UsageEvent.id),
            func.coalesce(func.sum(UsageEvent.duration_ms), 0),
            func.coalesce(func.sum(UsageEvent.inference_ms), 0),
            func.coalesce(func.sum(UsageEvent.queue_ms), 0),
            func.coalesce(func.sum(UsageEvent.raw_chars), 0),
            func.coalesce(func.sum(UsageEvent.compiled_chars), 0),
            func.coalesce(func.sum(UsageEvent.output_chars), 0),
        ).where(UsageEvent.user_id == user.id, UsageEvent.created_at >= since)
    ).one()
    events, duration_ms, inference_ms, queue_ms, raw_chars, compiled_chars, output_chars = map(int, row)
    saved = 0.0 if raw_chars <= 0 else max(0.0, min(100.0, (1 - compiled_chars / raw_chars) * 100))
    quota = get_or_create_quota(db, user, request.app.state.settings)
    monthly_used = compute_seconds_used(db, user.id)
    db.commit()
    return UsageSummary(
        events=events,
        duration_ms=duration_ms,
        inference_ms=inference_ms,
        queue_ms=queue_ms,
        raw_chars=raw_chars,
        compiled_chars=compiled_chars,
        output_chars=output_chars,
        context_saved_percent=round(saved, 2),
        monthly_compute_seconds_used=monthly_used,
        monthly_compute_seconds_limit=quota.monthly_compute_seconds_limit,
        plan=quota.plan,
    )
