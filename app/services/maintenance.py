from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import and_, delete, or_
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import ApiRateLimitWindow, AuthSession, BackgroundJob, SearchQueryCache, SystemHealthSnapshot
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

    db.commit()
    return counts


def run_maintenance_tick(settings) -> dict[str, Any]:
    started = utcnow()
    with SessionLocal() as db:
        counts = cleanup_ephemeral_state(db, settings, now=started)
    return {"status": "stable", "cleaned": counts, "finished_at": utcnow().isoformat()}


async def maintenance_loop(app) -> None:
    settings = app.state.settings
    interval = max(300.0, float(getattr(settings, "maintenance_interval_seconds", 3600.0)))
    # Give migrations/startup probes time to settle, but establish a first
    # successful tick soon enough for liveness diagnostics.
    await asyncio.sleep(min(30.0, interval))
    while True:
        try:
            result = await asyncio.to_thread(run_maintenance_tick, settings)
            app.state.maintenance_last_result = result
            app.state.maintenance_last_ok_at = utcnow()
            app.state.maintenance_last_error = ""
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            app.state.maintenance_last_error = f"{type(exc).__name__}: {exc}"[:500]
            logger.exception("X1 maintenance tick failed")
        await asyncio.sleep(interval)
