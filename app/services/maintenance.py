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
    DocumentArtifact,
    DocumentQAEvent,
    DocumentRevision,
    ImageEditRequest,
    ImageGeneration,
    SearchQueryCache,
    SystemCheckpoint,
    SystemHealthSnapshot,
)
from app.services.autonomous_development import recover_abandoned_slots
from app.services.jobs import reap_exhausted_jobs

logger = logging.getLogger(__name__)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _days(value: Any, minimum: int = 1) -> int:
    return max(minimum, int(value))


def _hours(value: Any, minimum: int = 1) -> int:
    return max(minimum, int(value))


def _recover_interrupted_image_jobs(db: Session, *, now: datetime) -> int:
    """Make terminal job failure visible on the image domain rows.

    A worker that dies on its final lease attempt cannot execute its normal
    failure handler. The generic job reaper therefore has to reconcile the
    linked generation/edit ledger or clients would poll `generating` forever.
    """
    changed = 0
    rows = db.execute(
        select(ImageGeneration, BackgroundJob)
        .join(BackgroundJob, BackgroundJob.id == ImageGeneration.job_id)
        .where(
            ImageGeneration.status.in_(["queued", "generating"]),
            BackgroundJob.status == "failed",
        )
        .limit(500)
    ).all()
    for generation, job in rows:
        reason = (job.error_message or "Image worker job exhausted its retry budget")[:1600]
        generation.status = "failed"
        generation.qa_status = "failed"
        generation.error_message = reason
        generation.finished_at = generation.finished_at or now
        edit = db.scalar(select(ImageEditRequest).where(ImageEditRequest.generation_id == generation.id))
        if edit is not None and edit.status not in {"ready", "failed", "cancelled"}:
            edit.status = "failed"
            edit.error_message = reason
            edit.qa_summary = {
                **(edit.qa_summary or {}),
                "passed": False,
                "recovered_after_worker_failure": True,
                "reason": reason[:500],
            }
            edit.updated_at = now
        changed += 1

    # Repair legacy/orphaned rows whose BackgroundJob was already removed by an
    # older retention pass. Current image creation always assigns job_id before
    # commit, so a sufficiently old active row with no job cannot make progress.
    orphan_cutoff = now - timedelta(minutes=15)
    orphans = list(
        db.scalars(
            select(ImageGeneration)
            .where(
                ImageGeneration.status.in_(["queued", "generating"]),
                ImageGeneration.job_id.is_(None),
                ImageGeneration.created_at < orphan_cutoff,
            )
            .limit(200)
        ).all()
    )
    for generation in orphans:
        reason = "Image job state was lost during an earlier interrupted worker lifecycle"
        generation.status = "failed"
        generation.qa_status = "failed"
        generation.error_message = reason
        generation.finished_at = generation.finished_at or now
        edit = db.scalar(select(ImageEditRequest).where(ImageEditRequest.generation_id == generation.id))
        if edit is not None and edit.status not in {"ready", "failed", "cancelled"}:
            edit.status = "failed"
            edit.error_message = reason
            edit.qa_summary = {**(edit.qa_summary or {}), "passed": False, "recovered_orphan": True}
            edit.updated_at = now
        changed += 1
    return changed


def _recover_stale_file_processing(db: Session, settings, *, now: datetime) -> int:
    # Parsing is killable and normally bounded by file_parse_timeout_seconds.
    # Five extra minutes cover queueing, DB scheduling and slow filesystem flushes.
    timeout = max(900, int(getattr(settings, "file_parse_timeout_seconds", 45)) + 300)
    from app.services.files import recover_stale_processing

    return recover_stale_processing(db, timeout_seconds=timeout, now=now)


def _recover_stale_document_qa(db: Session, settings, *, now: datetime) -> int:
    # A QA run can perform one initial render plus bounded repairs. Use the
    # durable qa_started event rather than revision.created_at because an old
    # document may legitimately start QA much later.
    attempts = 1 + max(0, min(2, int(getattr(settings, "document_qa_max_repairs", 1))))
    timeout = max(600, attempts * int(getattr(settings, "document_render_timeout_seconds", 60)) + 300)
    cutoff = now - timedelta(seconds=timeout)
    rows = list(
        db.scalars(
            select(DocumentRevision)
            .where(DocumentRevision.qa_status == "running")
            .limit(200)
        ).all()
    )
    recovered = 0
    for revision in rows:
        started = db.scalar(
            select(DocumentQAEvent)
            .where(DocumentQAEvent.revision_id == revision.id, DocumentQAEvent.gate == "qa_started")
            .order_by(DocumentQAEvent.created_at.desc())
            .limit(1)
        )
        started_at = _aware(started.created_at) if started is not None else None
        if started_at is not None and started_at >= cutoff:
            continue
        revision.qa_status = "pending"
        artifact = db.get(DocumentArtifact, revision.artifact_id)
        if artifact is not None and artifact.current_revision == revision.revision and artifact.status == "qa_running":
            artifact.status = "draft"
        db.add(
            DocumentQAEvent(
                revision_id=revision.id,
                gate="qa_recovered",
                status="failed",
                details={
                    "status": "failed",
                    "code": "qa_interrupted",
                    "message": "QA process disappeared before finalization; revision returned to pending for a safe retry",
                    "recovered_at": now.isoformat(),
                },
            )
        )
        recovered += 1
    return recovered


def cleanup_ephemeral_state(db: Session, settings, *, now: datetime | None = None) -> dict[str, int]:
    current = now or utcnow()
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)

    counts: dict[str, int] = {}
    counts["reaped_exhausted_jobs"] = reap_exhausted_jobs(db)
    # Reconcile dependent state before old terminal BackgroundJob rows are
    # deleted below. Ordering here is part of the durability contract.
    counts["recovered_image_jobs"] = _recover_interrupted_image_jobs(db, now=current)
    counts["recovered_file_processing"] = _recover_stale_file_processing(db, settings, now=current)
    counts["recovered_document_qa"] = _recover_stale_document_qa(db, settings, now=current)
    counts["recovered_autonomous_leases"] = recover_abandoned_slots(db, stale_minutes=10)

    api_cutoff = current - timedelta(hours=_hours(getattr(settings, "api_rate_window_retention_hours", 2)))
    result = db.execute(delete(ApiRateLimitWindow).where(ApiRateLimitWindow.window_start < api_cutoff))
    counts["api_rate_windows"] = int(result.rowcount or 0)

    search_cutoff = current - timedelta(hours=_hours(getattr(settings, "search_cache_retention_hours", 24)))
    result = db.execute(delete(SearchQueryCache).where(SearchQueryCache.created_at < search_cutoff))
    counts["search_cache"] = int(result.rowcount or 0)

    session_cutoff = current - timedelta(days=_days(getattr(settings, "expired_session_retention_days", 7)))
    result = db.execute(delete(AuthSession).where(or_(AuthSession.expires_at < session_cutoff, and_(AuthSession.revoked_at.is_not(None), AuthSession.revoked_at < session_cutoff))))
    counts["auth_sessions"] = int(result.rowcount or 0)

    health_cutoff = current - timedelta(days=_days(getattr(settings, "system_health_snapshot_retention_days", 30)))
    result = db.execute(delete(SystemHealthSnapshot).where(SystemHealthSnapshot.created_at < health_cutoff))
    counts["health_snapshots"] = int(result.rowcount or 0)

    succeeded_cutoff = current - timedelta(days=_days(getattr(settings, "background_job_success_retention_days", 30)))
    result = db.execute(delete(BackgroundJob).where(BackgroundJob.status.in_(["succeeded", "cancelled"]), BackgroundJob.finished_at.is_not(None), BackgroundJob.finished_at < succeeded_cutoff))
    counts["completed_jobs"] = int(result.rowcount or 0)

    failed_cutoff = current - timedelta(days=_days(getattr(settings, "background_job_failure_retention_days", 90)))
    result = db.execute(delete(BackgroundJob).where(BackgroundJob.status == "failed", BackgroundJob.finished_at.is_not(None), BackgroundJob.finished_at < failed_cutoff))
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
            _write_checkpoint(db, status="failed", message="Ephemeral-state maintenance failed", details={"error": error[:500], "cleanup_interval_seconds": interval})
            db.commit()
    except Exception:
        logger.exception("X1 could not persist maintenance failure checkpoint")


def _write_heartbeat(last_result: dict[str, Any] | None, interval: float) -> None:
    try:
        with SessionLocal() as db:
            _write_checkpoint(db, status="stable", message="Ephemeral-state maintenance scheduler is alive", details={"last_cleanup": last_result or {}, "cleanup_interval_seconds": interval})
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
