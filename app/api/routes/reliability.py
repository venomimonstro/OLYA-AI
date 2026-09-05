from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import SystemHealthSnapshot, User
from app.services.admin import audit, require_admin
from app.services.system_observability import collect_system_health, latest_checkpoints

router = APIRouter(prefix="/v1/admin/reliability", tags=["admin-reliability"])


@router.get("/status")
async def reliability_status(request: Request, refresh: bool = Query(default=True), deep: bool = Query(default=False),
                             admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> dict:
    stale = int(getattr(request.app.state.settings, "health_checkpoint_stale_seconds", 300))
    if refresh:
        result = await collect_system_health(request.app, db, persist=True, deep=deep)
        audit(db, admin, "reliability.refresh", "system", "x1", {"deep": deep, "status": result["status"]})
        db.commit(); result["checkpoints"] = latest_checkpoints(db, stale_after_seconds=stale); return result
    checkpoints = latest_checkpoints(db, stale_after_seconds=stale)
    latest = db.scalar(select(SystemHealthSnapshot).order_by(SystemHealthSnapshot.created_at.desc()).limit(1))
    return {"status": latest.overall_status if latest else "unknown", "score": latest.score if latest else 0,
            "stable": latest.stable_count if latest else 0, "degraded": latest.degraded_count if latest else 0,
            "failed": latest.failed_count if latest else 0, "critical": latest.failed_count if latest else 0,
            "unknown": sum(x.get("status") == "unknown" for x in checkpoints),
            "critical_failed": latest.critical_failed_count if latest else 0,
            "checked_at": latest.created_at if latest else None, "snapshot_id": latest.id if latest else None,
            "checkpoints": checkpoints}


@router.post("/run")
async def run_deep_reliability_check(request: Request, admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> dict:
    result = await collect_system_health(request.app, db, persist=True, deep=True)
    audit(db, admin, "reliability.deep_run", "system", "x1", {"status": result["status"], "score": result["score"]})
    db.commit(); result["checkpoints"] = latest_checkpoints(db, stale_after_seconds=int(getattr(request.app.state.settings,"health_checkpoint_stale_seconds",300))); return result


@router.get("/history")
def reliability_history(limit: int = Query(default=50, ge=1, le=200), admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> list[dict]:
    rows = list(db.scalars(select(SystemHealthSnapshot).order_by(SystemHealthSnapshot.created_at.desc()).limit(limit)).all())
    return [{"id": x.id, "status": x.overall_status, "score": x.score, "stable": x.stable_count,
             "degraded": x.degraded_count, "failed": x.failed_count, "critical_failed": x.critical_failed_count,
             "created_at": x.created_at} for x in rows]
