from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import AutonomousDevelopmentLedger, BackgroundJob, ChatRun, DocumentRevision, ImageGeneration, SystemCheckpoint
from app.services.maintenance import cleanup_ephemeral_state


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def recovery_integrity_snapshot(db: Session, settings, *, now: datetime | None = None) -> dict:
    current = now or utcnow()
    chat_cutoff = current - timedelta(minutes=15)
    agent_cutoff = current - timedelta(minutes=10)

    expired_live_jobs = int(
        db.scalar(
            select(func.count()).select_from(BackgroundJob).where(
                BackgroundJob.status.in_(["leased", "running"]),
                BackgroundJob.lease_expires_at.is_not(None),
                BackgroundJob.lease_expires_at <= current,
            )
        )
        or 0
    )
    stale_chat_runs = int(
        db.scalar(
            select(func.count()).select_from(ChatRun).where(
                ChatRun.status == "running",
                ChatRun.updated_at < chat_cutoff,
            )
        )
        or 0
    )
    stale_agent_slots = int(
        db.scalar(
            select(func.count()).select_from(AutonomousDevelopmentLedger).where(
                AutonomousDevelopmentLedger.active_subagents > 0,
                AutonomousDevelopmentLedger.last_heartbeat_at < agent_cutoff,
            )
        )
        or 0
    )
    orphan_active_images = int(
        db.scalar(
            select(func.count()).select_from(ImageGeneration).where(
                ImageGeneration.status.in_(["queued", "generating"]),
                ImageGeneration.job_id.is_(None),
                ImageGeneration.created_at < chat_cutoff,
            )
        )
        or 0
    )
    running_document_qa = int(
        db.scalar(select(func.count()).select_from(DocumentRevision).where(DocumentRevision.qa_status == "running")) or 0
    )
    maintenance = db.get(SystemCheckpoint, "ops.maintenance")
    maintenance_fresh = bool(
        maintenance
        and maintenance.status == "stable"
        and maintenance.last_checked_at
        and (current - (maintenance.last_checked_at if maintenance.last_checked_at.tzinfo else maintenance.last_checked_at.replace(tzinfo=timezone.utc))) < timedelta(minutes=10)
    )

    blockers = []
    if expired_live_jobs: blockers.append("expired_live_jobs")
    if stale_chat_runs: blockers.append("stale_chat_runs")
    if stale_agent_slots: blockers.append("stale_agent_slots")
    if orphan_active_images: blockers.append("orphan_active_images")
    if not maintenance_fresh and str(getattr(settings, "env", "development")).lower() in {"production", "prod", "stable"}:
        blockers.append("maintenance_checkpoint_not_fresh")

    return {
        "generated_at": current.isoformat(),
        "ready": not blockers,
        "blockers": blockers,
        "counts": {
            "expired_live_jobs": expired_live_jobs,
            "stale_chat_runs": stale_chat_runs,
            "stale_agent_slots": stale_agent_slots,
            "orphan_active_images": orphan_active_images,
            "running_document_qa": running_document_qa,
        },
        "maintenance": {
            "status": maintenance.status if maintenance else "missing",
            "last_checked_at": maintenance.last_checked_at if maintenance else None,
            "fresh": maintenance_fresh,
        },
        "contracts": {
            "background_jobs": "lease_token + idempotency_key + retry_budget",
            "chat": "canonical message/usage commit atomically terminalizes ChatRun",
            "images": "terminal job failure reconciles generation/edit ledgers",
            "documents": "stale running QA returns to pending with recovery event",
            "agents": "stale active-subagent leases are cleared; checkpoints remain durable",
        },
    }


def reconcile_and_snapshot(db: Session, settings, *, now: datetime | None = None) -> dict:
    """Run safe maintenance reconciliation in caller transaction, then inspect blockers."""
    current = now or utcnow()
    recovered = cleanup_ephemeral_state(db, settings, now=current)
    snapshot = recovery_integrity_snapshot(db, settings, now=current)
    return {"recovered": recovered, "integrity": snapshot}
