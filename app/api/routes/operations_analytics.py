from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import User
from app.services.admin import require_admin
from app.services.operations_analytics import operations_summary
from app.services.server_profiles import profile_payload, read_active_profile, read_staged_profile, stage_profile_request
from app.services.system_observability import collect_system_health, latest_checkpoints

router = APIRouter(prefix="/v1/admin/operations", tags=["admin-operations"])


class ServerProfileRequest(BaseModel):
    profile: Literal["super_low", "optimal", "maximum"]


@router.get('/summary')
def summary(request: Request, window_hours: int = Query(default=24, ge=1, le=24*90), admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> dict:
    _ = admin
    return operations_summary(db, window_hours=window_hours, monthly_server_cost_rub=float(getattr(request.app.state.settings,'monthly_server_cost_rub',4000.0)))


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
