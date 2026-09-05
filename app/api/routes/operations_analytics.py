from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import User
from app.services.admin import require_admin
from app.services.operations_analytics import operations_summary
from app.services.system_observability import collect_system_health, latest_checkpoints

router = APIRouter(prefix="/v1/admin/operations", tags=["admin-operations"])

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
