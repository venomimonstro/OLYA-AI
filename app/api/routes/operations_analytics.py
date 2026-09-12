from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import (
    AnswerAudit,
    ApiKey,
    ApiRequestTelemetry,
    BackgroundJob,
    BillingCheckout,
    BillingSubscription,
    FrustrationEvent,
    RiskEvent,
    SafetyCase,
    User,
)
from app.models_sprint30 import ComplaintCase, RegressionCase
from app.services.admin import require_admin
from app.services.operations_analytics import operations_summary
from app.services.server_profiles import profile_payload, read_active_profile, read_staged_profile, stage_profile_request
from app.services.system_observability import collect_system_health, latest_checkpoints

router = APIRouter(prefix="/v1/admin/operations", tags=["admin-operations"])


class ServerProfileRequest(BaseModel):
    profile: Literal["super_low", "optimal", "maximum"]


def _count(db: Session, model, *conditions) -> int:
    stmt = select(func.count()).select_from(model)
    if conditions:
        stmt = stmt.where(*conditions)
    return int(db.scalar(stmt) or 0)


@router.get('/summary')
def summary(request: Request, window_hours: int = Query(default=24, ge=1, le=24*90), admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> dict:
    _ = admin
    return operations_summary(db, window_hours=window_hours, monthly_server_cost_rub=float(getattr(request.app.state.settings,'monthly_server_cost_rub',4000.0)))


@router.get('/control-center')
def control_center(
    request: Request,
    window_hours: int = Query(default=24, ge=1, le=24 * 30),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    """Read-only operator snapshot built from canonical domain state.

    This endpoint deliberately aggregates existing services/tables instead of
    creating a second admin state machine. Mutating user operations remain in
    their dedicated routes and Sprint 72 UI.
    """
    _ = admin
    now = datetime.now(timezone.utc)
    since = now - timedelta(hours=window_hours)
    ops = operations_summary(
        db,
        window_hours=window_hours,
        monthly_server_cost_rub=float(getattr(request.app.state.settings, 'monthly_server_cost_rub', 4000.0)),
    )

    api_requests = _count(db, ApiRequestTelemetry, ApiRequestTelemetry.created_at >= since)
    api_errors = _count(
        db,
        ApiRequestTelemetry,
        ApiRequestTelemetry.created_at >= since,
        ApiRequestTelemetry.status_code >= 400,
    )
    api_latency = float(
        db.scalar(select(func.coalesce(func.avg(ApiRequestTelemetry.latency_ms), 0)).where(ApiRequestTelemetry.created_at >= since))
        or 0
    )
    api_cost = int(
        db.scalar(
            select(func.coalesce(func.sum(ApiRequestTelemetry.cost_microunits), 0)).where(
                ApiRequestTelemetry.created_at >= since
            )
        )
        or 0
    )

    lanes = getattr(request.app.state, 'overload_lanes', {}) or {}
    overload = {}
    for name, lane in lanes.items():
        snap = lane.snapshot()
        overload[name] = {
            'active': int(snap.active),
            'waiting': int(snap.waiting),
            'max_concurrent': int(snap.max_concurrent),
            'max_queue': int(snap.max_queue),
            'breaker_state': str(snap.breaker_state),
            'breaker_failures': int(snap.breaker_failures),
        }

    return {
        'generated_at': now,
        'window_hours': window_hours,
        'accounts': {
            'users': _count(db, User),
            'active_users': _count(db, User, User.is_active.is_(True)),
            'admins': _count(db, User, User.is_admin.is_(True)),
        },
        'traffic': ops.get('traffic', {}),
        'economics': ops.get('economics', {}),
        'resources': ops.get('resources', {}),
        'workloads': {
            'jobs_queued': _count(db, BackgroundJob, BackgroundJob.status == 'queued'),
            'jobs_running': _count(db, BackgroundJob, BackgroundJob.status == 'running'),
            'jobs_failed': _count(db, BackgroundJob, BackgroundJob.status == 'failed'),
            'images': ops.get('images', {}),
            'agents': ops.get('agents', {}),
        },
        'billing': {
            'active_subscriptions': _count(
                db,
                BillingSubscription,
                BillingSubscription.status == 'active',
                BillingSubscription.current_period_end > now,
            ),
            'past_due_active_rows': _count(
                db,
                BillingSubscription,
                BillingSubscription.status == 'active',
                BillingSubscription.current_period_end <= now,
            ),
            'cancel_at_period_end': _count(
                db,
                BillingSubscription,
                BillingSubscription.status == 'active',
                BillingSubscription.cancel_at_period_end.is_(True),
            ),
            'pending_checkouts': _count(
                db,
                BillingCheckout,
                BillingCheckout.status == 'pending',
                BillingCheckout.expires_at > now,
            ),
            'paid_checkouts_window': _count(
                db,
                BillingCheckout,
                BillingCheckout.status == 'paid',
                BillingCheckout.created_at >= since,
            ),
            'refunded_checkouts_window': _count(
                db,
                BillingCheckout,
                BillingCheckout.status == 'refunded',
                BillingCheckout.created_at >= since,
            ),
        },
        'api': {
            'active_keys': _count(db, ApiKey, ApiKey.status == 'active'),
            'requests': api_requests,
            'errors': api_errors,
            'error_rate': round(api_errors / max(1, api_requests), 4),
            'avg_latency_ms': round(api_latency, 2),
            'resource_cost_microunits': api_cost,
        },
        'signals': {
            'open_complaints': _count(db, ComplaintCase, ComplaintCase.status.notin_(['resolved', 'rejected'])),
            'release_blocking_regressions': _count(db, RegressionCase, RegressionCase.release_blocking.is_(True)),
            'open_frustration_events': _count(db, FrustrationEvent, FrustrationEvent.resolved.is_(False)),
            'open_safety_cases': _count(db, SafetyCase, SafetyCase.status.in_(['open', 'reviewing', 'restricted'])),
            'open_risk_events': _count(db, RiskEvent, RiskEvent.state.in_(['new', 'reviewing'])),
            'failed_answer_audits': _count(db, AnswerAudit, AnswerAudit.status == 'failed'),
        },
        'overload': overload,
    }


@router.get('/health')
async def health(request: Request, refresh: bool = Query(default=True), deep: bool = Query(default=False), admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> dict:
    _ = admin
    stale = int(getattr(request.app.state.settings, 'health_checkpoint_stale_seconds', 300))
    if refresh:
        result = await collect_system_health(request.app, db, persist=True, deep=deep)
        db.commit()
        result['checkpoints'] = latest_checkpoints(db, stale_after_seconds=stale)
        return result
    return {'status': 'unknown', 'checkpoints': latest_checkpoints(db, stale_after_seconds=stale)}


@router.get('/overload')
def overload(request: Request, admin: User = Depends(require_admin)) -> dict:
    _ = admin
    lanes = getattr(request.app.state, 'overload_lanes', {}) or {}
    return {
        'lanes': {
            name: {
                'active': snap.active,
                'waiting': snap.waiting,
                'max_concurrent': snap.max_concurrent,
                'max_queue': snap.max_queue,
                'breaker_state': snap.breaker_state,
                'breaker_failures': snap.breaker_failures,
            }
            for name, lane in lanes.items()
            for snap in [lane.snapshot()]
        }
    }


def _profile_host(request: Request) -> tuple[float, int]:
    active = read_active_profile(request.app.state.settings.data_root)
    envelope = (active or {}).get('envelope') or {}
    try:
        ram = float(envelope['host_ram_gib'])
        cores = int(envelope['cpu_cores'])
    except (KeyError, TypeError, ValueError):
        raise HTTPException(
            status_code=409,
            detail='Active server boot envelope is unavailable. Apply a profile on the host before staging runtime changes.',
        )
    return ram, cores


@router.get('/server-profile')
def server_profile_status(request: Request, admin: User = Depends(require_admin)) -> dict:
    _ = admin
    settings = request.app.state.settings
    active = read_active_profile(settings.data_root)
    staged = read_staged_profile(settings.data_root)
    return {
        'configured_profile': settings.server_optimization_profile,
        'active': active,
        'staged': staged,
        'apply_contract': 'staged profile is applied on the next controlled host restart; live semaphores are never replaced in-place',
    }


@router.get('/server-profile/preview')
def server_profile_preview(
    request: Request,
    profile: Literal['super_low', 'optimal', 'maximum'] = Query(...),
    admin: User = Depends(require_admin),
) -> dict:
    _ = admin
    ram, cores = _profile_host(request)
    return profile_payload(profile, ram, cores)


@router.post('/server-profile')
def stage_server_profile(
    payload: ServerProfileRequest,
    request: Request,
    admin: User = Depends(require_admin),
) -> dict:
    _ = admin
    ram, cores = _profile_host(request)
    target = stage_profile_request(request.app.state.settings.data_root, payload.profile, ram, cores)
    return {
        'status': 'staged',
        'profile': payload.profile,
        'restart_required': True,
        'active_operations_unchanged': True,
        'path': str(target),
        'next_step': 'On the host run: python3 scripts/apply_server_profile.py --staged && docker compose up -d',
    }
