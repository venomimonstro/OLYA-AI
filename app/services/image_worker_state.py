from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import SystemCheckpoint


CHECKPOINT_KEY = "worker.image"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def write_image_worker_heartbeat(db: Session, *, details: dict, status: str = "stable", message: str = "Image worker is alive") -> None:
    row = db.scalar(select(SystemCheckpoint).where(SystemCheckpoint.key == CHECKPOINT_KEY))
    now = utcnow()
    if row is None:
        row = SystemCheckpoint(key=CHECKPOINT_KEY, subsystem="images")
        db.add(row)
        db.flush()
    row.status = status
    row.severity = "info" if status == "stable" else "warning"
    row.critical = False
    row.message = message[:500]
    row.details = dict(details or {})
    row.last_checked_at = now
    if status == "stable":
        row.last_ok_at = now
        row.consecutive_failures = 0
    else:
        row.consecutive_failures = int(row.consecutive_failures or 0) + 1
    db.flush()


def image_worker_snapshot(db: Session, *, stale_seconds: int = 90) -> dict:
    row = db.scalar(select(SystemCheckpoint).where(SystemCheckpoint.key == CHECKPOINT_KEY))
    if row is None or row.last_checked_at is None:
        return {"alive": False, "status": "missing", "last_checked_at": None, "details": {}}
    checked = row.last_checked_at
    if checked.tzinfo is None:
        checked = checked.replace(tzinfo=timezone.utc)
    age = max(0.0, (utcnow() - checked).total_seconds())
    alive = row.status == "stable" and age <= max(15, int(stale_seconds))
    return {
        "alive": alive,
        "status": row.status if alive else "stale",
        "age_seconds": round(age, 1),
        "last_checked_at": checked.isoformat(),
        "details": dict(row.details or {}),
    }
