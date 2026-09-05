from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.orm import Session

from app.models import BackgroundJob, UserQuota


class JobLeaseLostError(RuntimeError):
    pass


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def enqueue_job(
    db: Session,
    *,
    kind: str,
    payload: dict | None = None,
    user_id: str | None = None,
    project_id: str | None = None,
    task_id: str | None = None,
    priority: int = 100,
    max_attempts: int = 3,
    idempotency_key: str | None = None,
    available_at: datetime | None = None,
) -> BackgroundJob:
    if idempotency_key:
        existing = db.scalar(select(BackgroundJob).where(BackgroundJob.idempotency_key == idempotency_key))
        if existing is not None:
            return existing
    job = BackgroundJob(
        kind=kind.strip(), payload=payload or {}, user_id=user_id, project_id=project_id, task_id=task_id,
        priority=priority, max_attempts=max_attempts, idempotency_key=idempotency_key, available_at=available_at or utcnow(),
    )
    db.add(job); db.flush(); return job


def _ready_clause(now: datetime):
    return or_(
        and_(BackgroundJob.status == "queued", BackgroundJob.available_at <= now),
        and_(BackgroundJob.status.in_(["leased", "running"]), BackgroundJob.lease_expires_at.is_not(None), BackgroundJob.lease_expires_at <= now),
    )


def lease_next_job(db: Session, *, worker_id: str, lease_seconds: int = 120, kinds: set[str] | None = None) -> BackgroundJob | None:
    now = utcnow()
    candidates = list(db.scalars(select(BackgroundJob).where(_ready_clause(now), BackgroundJob.attempt_count < BackgroundJob.max_attempts, *([BackgroundJob.kind.in_(kinds)] if kinds else [])).order_by(BackgroundJob.priority.asc(), BackgroundJob.available_at.asc(), BackgroundJob.created_at.asc()).limit(8)).all())
    for candidate in candidates:
        if candidate.user_id:
            quota = db.get(UserQuota, candidate.user_id)
            limit = max(1, quota.max_concurrent_jobs if quota is not None else 1)
            active_for_user = db.scalar(select(func.count(BackgroundJob.id)).where(BackgroundJob.user_id == candidate.user_id, BackgroundJob.id != candidate.id, BackgroundJob.status.in_(["leased", "running"]), BackgroundJob.lease_expires_at > now)) or 0
            if int(active_for_user) >= limit:
                continue
        old_status = candidate.status; old_lease = candidate.lease_expires_at
        token = secrets.token_hex(24); expiry = now + timedelta(seconds=lease_seconds)
        predicates = [BackgroundJob.id == candidate.id, BackgroundJob.status == old_status]
        predicates.append(BackgroundJob.lease_expires_at.is_(None) if old_lease is None else BackgroundJob.lease_expires_at == old_lease)
        result = db.execute(update(BackgroundJob).execution_options(synchronize_session=False).where(*predicates).values(status="leased", lease_owner=worker_id, lease_token=token, lease_expires_at=expiry, heartbeat_at=now, attempt_count=BackgroundJob.attempt_count + 1, error_message=""))
        if result.rowcount == 1:
            db.flush(); return db.get(BackgroundJob, candidate.id, populate_existing=True)
        db.expire_all()
    return None


def start_job(db: Session, job: BackgroundJob, *, worker_id: str, lease_token: str) -> None:
    now = utcnow()
    result = db.execute(update(BackgroundJob).execution_options(synchronize_session=False).where(BackgroundJob.id == job.id, BackgroundJob.status == "leased", BackgroundJob.lease_owner == worker_id, BackgroundJob.lease_token == lease_token, BackgroundJob.lease_expires_at > now).values(status="running", started_at=now, heartbeat_at=now))
    if result.rowcount != 1:
        raise JobLeaseLostError("Job lease is no longer valid")
    db.flush()


def heartbeat_job(db: Session, job_id: str, *, worker_id: str, lease_token: str, lease_seconds: int = 120) -> None:
    now = utcnow()
    result = db.execute(update(BackgroundJob).execution_options(synchronize_session=False).where(BackgroundJob.id == job_id, BackgroundJob.status.in_(["leased", "running"]), BackgroundJob.lease_owner == worker_id, BackgroundJob.lease_token == lease_token, BackgroundJob.lease_expires_at > now).values(heartbeat_at=now, lease_expires_at=now + timedelta(seconds=lease_seconds)))
    if result.rowcount != 1:
        raise JobLeaseLostError("Job lease is no longer valid")
    db.flush()


def complete_job(db: Session, job_id: str, *, worker_id: str, lease_token: str, result_payload: dict | None = None) -> None:
    now = utcnow()
    result = db.execute(update(BackgroundJob).execution_options(synchronize_session=False).where(BackgroundJob.id == job_id, BackgroundJob.status.in_(["leased", "running"]), BackgroundJob.lease_owner == worker_id, BackgroundJob.lease_token == lease_token).values(status="succeeded", result=result_payload or {}, finished_at=now, heartbeat_at=now, lease_owner=None, lease_token=None, lease_expires_at=None))
    if result.rowcount != 1:
        raise JobLeaseLostError("Job lease is no longer valid")
    db.flush()


def fail_job(db: Session, job_id: str, *, worker_id: str, lease_token: str, error_message: str, retry_delay_seconds: int = 5) -> None:
    now = utcnow(); job = db.get(BackgroundJob, job_id)
    if job is None or job.lease_owner != worker_id or job.lease_token != lease_token:
        raise JobLeaseLostError("Job lease is no longer valid")
    retry = job.attempt_count < job.max_attempts
    job.status = "queued" if retry else "failed"; job.available_at = now + timedelta(seconds=retry_delay_seconds) if retry else now
    job.finished_at = None if retry else now; job.error_message = error_message[:2000]
    job.lease_owner = None; job.lease_token = None; job.lease_expires_at = None; job.heartbeat_at = now; db.flush()


def reap_exhausted_jobs(db: Session) -> int:
    now = utcnow()
    result = db.execute(update(BackgroundJob).execution_options(synchronize_session=False).where(BackgroundJob.status.in_(["leased", "running"]), BackgroundJob.lease_expires_at.is_not(None), BackgroundJob.lease_expires_at <= now, BackgroundJob.attempt_count >= BackgroundJob.max_attempts).values(status="failed", finished_at=now, error_message="Job lease expired after maximum attempts", lease_owner=None, lease_token=None, lease_expires_at=None))
    db.flush(); db.expire_all(); return int(result.rowcount or 0)
