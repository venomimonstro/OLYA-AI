from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import and_, delete, or_, select
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import (
    ApiRateLimitWindow,
    AuthSession,
    BackgroundJob,
    SearchQueryCache,
    SystemCheckpoint,
    SystemHealthSnapshot,
)
from app.services.jobs import reap_exhausted_jobs

logger = logging.getLogger(__name__)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _days(value: Any, minimum: int = 1) -> int:
    return max(minimum, int(value))


def _hours(value: Any, minimum: int = 1) -> int:
    return max(minimum, int(value))


def cleanup_ephemeral_state(db: Session, settings, *, now: datetime | None = None) -> dict[str, int]:
    """Bound tables whose rows have no long-term business/audit value.

    Payment/resource ledgers, complaints, user content, beta history and other
    business records are intentionally excluded. Every deletion below targets a
    cache, rate window, expired/revoked session, health snapshot, or completed
    background-job envelope whose useful lifetime is explicitly bounded.
    """
    current = now or utcnow()
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)

    counts: dict[str, int] = {}
    counts["reaped_exhausted_jobs"] = reap_exhausted_jobs(db)

    api_cutoff = current - timedelta(hours=_hours(getattr(settings, "api_rate_window_retention_hours", 2)))
    result = db.execute(delete(ApiRateLimitWindow).where(ApiRateLimitWindow.window_start < api_cutoff))
    counts["api_rate_windows"] = int(result.rowcount or 0)

    search_cutoff = current - timedelta(hours=_hours(getattr(settings, "search_cache_retention_hours", 24)))
    result = db.execute(delete(SearchQueryCache).where(SearchQueryCache.created_at < search_cutoff))
    counts["search_cache"] = int(result.rowcount or 0)

    session_cutoff = current - timedelta(days=_days(getattr(settings, "expired_session_retention_days", 7)))
    result = db.execute(
        delete(AuthSession).where(
            or_(
                AuthSession.expires_at < session_cutoff,
                and_(AuthSession.revoked_at.is_not(None), AuthSession.revoked_at < session_cutoff),
            )
        )
    )
    counts["auth_sessions"] = int(result.rowcount or 0)

    health_cutoff = current - timedelta(days=_days(getattr(settings, "system_health_snapshot_retention_days", 30)))
    result = db.execute(delete(SystemHealthSnapshot).where(SystemHealthSnapshot.created_at < health_cutoff))
    counts["health_snapshots"] = int(result.rowcount or 0)

    succeeded_cutoff = current - timedelta(days=_days(getattr(settings, "background_job_success_retention_days", 30)))
    result = db.execute(
        delete(BackgroundJob).where(
            BackgroundJob.status.in_(["succeeded", "cancelled"]),
            BackgroundJob.finished_at.is_not(None),
            BackgroundJob.finished_at < succeeded_cutoff,
        )
    )
    counts["completed_jobs"] = int(result.rowcount or 0)

    failed_cutoff = current - timedelta(days=_days(getattr(settings, "background_job_failure_retention_days", 90)))
    result = db.execute(
        delete(BackgroundJob).where(
            BackgroundJob.status == "failed",
            BackgroundJob.finished_at.is_not(None),
            BackgroundJob.finished_at < failed_cutoff,
        )
    )
    counts["failed_jobs"] = int(result.rowcount or 0)
    return counts


def _write_checkpoint(db: Session, *, status: str, message: str, details: dict[str, Any]) -> None:
    row = db.scalar(select(SystemCheckpoint).where(SystemCheckpoint.key == "ops.maintenance"))
    now = utcnow()
    if row is None:
        row = SystemCheckpoint(key="ops.maintenance", subsystem="operations")
        db.add(row)
        db.flush()
    row.status = status
    row.severity = "warning" if status != "stable" else "info"
    row.critical = False
    row.message = message[:500]
    row.details = {**details, "recommended_action": "Inspect maintenance logs and PostgreSQL growth." if status != "stable" else ""}
    row.last_checked_at = now
    if status == "stable":
        row.consecutive_failures = 0
        row.last_ok_at = now
    else:
        row.consecutive_failures = int(row.consecutive_failures or 0) + 1


def run_maintenance_tick(settings) -> dict[str, Any]:
    started = utcnow()
    with SessionLocal() as db:
        try:
            counts = cleanup_ephemeral_state(db, settings, now=started)
            result = {"status": "stable", "cleaned": counts, "finished_at": utcnow().isoformat()}
            _write_checkpoint(db, status="stable", message="Ephemeral-state retention completed", details=result)
            db.commit()
            return result
        except Exception:
            db.rollback()
            raise


def _write_failure_checkpoint(error: str, interval: float) -> None:
    try:
        with SessionLocal() as db:
            _write_checkpoint(
                db,
                status="failed",
                message="Ephemeral-state maintenance failed",
                details={"error": error[:500], "cleanup_interval_seconds": interval},
            )
            db.commit()
    except Exception:
        # If PostgreSQL itself is unavailable, core.database will already expose
        # the root cause; a second exception must not kill the maintenance loop.
        logger.exception("X1 could not persist maintenance failure checkpoint")


def _write_heartbeat(last_result: dict[str, Any] | None, interval: float) -> None:
    try:
        with SessionLocal() as db:
            _write_checkpoint(
                db,
                status="stable",
                message="Ephemeral-state maintenance scheduler is alive",
                details={"last_cleanup": last_result or {}, "cleanup_interval_seconds": interval},
            )
            db.commit()
    except Exception:
        logger.exception("X1 maintenance heartbeat failed")


async def maintenance_loop(app) -> None:
    settings = app.state.settings
    cleanup_interval = max(300.0, float(getattr(settings, "maintenance_interval_seconds", 3600.0)))
    heartbeat_interval = min(240.0, cleanup_interval)
    next_cleanup = 0.0
    last_result: dict[str, Any] | None = None
    await asyncio.sleep(min(30.0, heartbeat_interval))
    while True:
        try:
            now_mono = time.monotonic()
            if now_mono >= next_cleanup:
                result = await asyncio.to_thread(run_maintenance_tick, settings)
                last_result = result
                app.state.maintenance_last_result = result
                app.state.maintenance_last_ok_at = utcnow()
                app.state.maintenance_last_error = ""
                next_cleanup = time.monotonic() + cleanup_interval
            else:
                await asyncio.to_thread(_write_heartbeat, last_result, cleanup_interval)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"[:500]
            app.state.maintenance_last_error = error
            await asyncio.to_thread(_write_failure_checkpoint, error, cleanup_interval)
            logger.exception("X1 maintenance tick failed")
        await asyncio.sleep(heartbeat_interval)
