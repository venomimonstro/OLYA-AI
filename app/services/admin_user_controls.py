from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AdminUserControl, User, UserQuota
from app.services.measured_plans import apply_runtime_plan_to_quota, runtime_plan_catalog


class AdminUserControlError(RuntimeError):
    pass


class AdminUserControlConflict(AdminUserControlError):
    pass


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def get_control(db: Session, user_id: str, *, lock: bool = False) -> AdminUserControl | None:
    stmt = select(AdminUserControl).where(AdminUserControl.user_id == user_id)
    return db.scalar(stmt.with_for_update() if lock else stmt)


def control_is_active(row: AdminUserControl | None, *, now: datetime | None = None) -> bool:
    if row is None:
        return False
    expires_at = _aware(row.expires_at)
    return expires_at is None or expires_at > (now or utcnow())


def control_dict(row: AdminUserControl | None) -> dict | None:
    if row is None:
        return None
    return {
        "user_id": row.user_id,
        "plan_override": row.plan_override,
        "monthly_compute_seconds_limit": row.monthly_compute_seconds_limit,
        "max_concurrent_inference": row.max_concurrent_inference,
        "max_concurrent_jobs": row.max_concurrent_jobs,
        "expires_at": row.expires_at,
        "reason": row.reason,
        "updated_by": row.updated_by,
        "version": row.version,
        "active": control_is_active(row),
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def _validate_plan(db: Session, settings, plan: str | None) -> None:
    if plan is None:
        return
    allowed = {str(item["name"]) for item in runtime_plan_catalog(db, settings)}
    if plan not in allowed:
        raise AdminUserControlError("Unknown or unavailable plan override")


def _validate_expiry(expires_at: datetime | None) -> None:
    if expires_at is None:
        return
    aware = _aware(expires_at)
    now = utcnow()
    if aware is None or aware <= now:
        raise AdminUserControlError("Override expiry must be in the future")
    if aware > now + timedelta(days=366):
        raise AdminUserControlError("Override expiry cannot exceed 366 days")


def apply_control_to_quota(db: Session, user: User, settings, quota: UserQuota) -> UserQuota:
    row = get_control(db, user.id)
    if not control_is_active(row):
        return quota
    if row.plan_override:
        quota = apply_runtime_plan_to_quota(db, user, settings, row.plan_override)
    if row.monthly_compute_seconds_limit is not None:
        quota.monthly_compute_seconds_limit = max(0, int(row.monthly_compute_seconds_limit))
    if row.max_concurrent_inference is not None:
        quota.max_concurrent_inference = max(1, int(row.max_concurrent_inference))
    if row.max_concurrent_jobs is not None:
        quota.max_concurrent_jobs = max(1, int(row.max_concurrent_jobs))
    return quota


def put_control(
    db: Session,
    user: User,
    settings,
    *,
    actor_user_id: str,
    expected_version: int,
    plan_override: str | None,
    monthly_compute_seconds_limit: int | None,
    max_concurrent_inference: int | None,
    max_concurrent_jobs: int | None,
    expires_at: datetime | None,
    reason: str,
) -> AdminUserControl:
    _validate_plan(db, settings, plan_override)
    _validate_expiry(expires_at)
    reason = reason.strip()
    if len(reason) < 3:
        raise AdminUserControlError("Override reason is required")
    if all(
        value is None
        for value in (
            plan_override,
            monthly_compute_seconds_limit,
            max_concurrent_inference,
            max_concurrent_jobs,
        )
    ):
        raise AdminUserControlError("At least one override must be configured")

    row = get_control(db, user.id, lock=True)
    current_version = int(row.version) if row is not None else 0
    if current_version != int(expected_version):
        raise AdminUserControlConflict(
            f"Admin user control changed concurrently; expected version {expected_version}, current version {current_version}"
        )
    if row is None:
        row = AdminUserControl(user_id=user.id, reason=reason, updated_by=actor_user_id)
        db.add(row)
    row.plan_override = plan_override
    row.monthly_compute_seconds_limit = monthly_compute_seconds_limit
    row.max_concurrent_inference = max_concurrent_inference
    row.max_concurrent_jobs = max_concurrent_jobs
    row.expires_at = expires_at
    row.reason = reason
    row.updated_by = actor_user_id
    row.updated_at = utcnow()
    db.flush()
    return row


def clear_control(db: Session, user: User, *, expected_version: int) -> None:
    row = get_control(db, user.id, lock=True)
    current_version = int(row.version) if row is not None else 0
    if row is None:
        if int(expected_version) != 0:
            raise AdminUserControlConflict(
                f"Admin user control changed concurrently; expected version {expected_version}, current version 0"
            )
        return
    if current_version != int(expected_version):
        raise AdminUserControlConflict(
            f"Admin user control changed concurrently; expected version {expected_version}, current version {current_version}"
        )
    db.delete(row)
    db.flush()
