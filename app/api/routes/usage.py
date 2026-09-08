from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.inference.router import choose_route
from app.models import UsageEvent, User
from app.services.auth import get_current_user
from app.services.budget_transparency import budget_snapshot
from app.services.conditional_verification import plan_verification
from app.services.freshness import classify_freshness
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


class BudgetPreviewRequest(BaseModel):
    message: str = Field(min_length=1, max_length=200_000)
    mode: str = "auto"
    verification: str = "auto"
    research_source_count: int = Field(default=0, ge=0, le=10)


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


@router.get("/budget")
def current_budget(
    request: Request,
    details: bool = Query(default=False),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    result = budget_snapshot(db, user, request.app.state.settings, include_details=details)
    db.commit()
    return result


@router.post("/budget-preview")
def budget_preview(
    payload: BudgetPreviewRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    settings = request.app.state.settings
    route = choose_route(payload.message, payload.mode, settings.max_context_tokens, settings.deep_context_tokens)
    freshness = classify_freshness(payload.message).required
    verification = plan_verification(
        verification=payload.verification,
        user_text=payload.message,
        route_mode=route.mode,
        requirements=[],
        freshness_required=freshness,
        verified_source_count=payload.research_source_count,
    )
    result = budget_snapshot(
        db,
        user,
        settings,
        projected_mode=route.mode,
        projected_verification_extra=verification.extra_inference_budget,
        include_details=False,
    )
    result["route"] = {
        "requested_mode": payload.mode,
        "selected_mode": route.mode,
        "reasoning": bool(route.reasoning),
        "verification_risk_score": verification.risk_score,
        "freshness_required": freshness,
    }
    db.commit()
    return result
