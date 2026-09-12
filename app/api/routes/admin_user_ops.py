from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import AdminAuditLog, AuthSession, User, UserRestriction
from app.services.admin import audit, require_admin
from app.services.admin_user_controls import (
    AdminUserControlConflict,
    AdminUserControlError,
    clear_control,
    control_dict,
    get_control,
    put_control,
)
from app.services.auth import normalize_email
from app.services.billing import get_subscription
from app.services.quota import get_or_create_quota

router = APIRouter(prefix="/v1/admin/users", tags=["admin-user-operations"])


class AdminUserControlPut(BaseModel):
    expected_version: int = Field(ge=0)
    plan_override: str | None = Field(default=None, max_length=24)
    monthly_compute_seconds_limit: int | None = Field(default=None, ge=0, le=31_536_000)
    max_concurrent_inference: int | None = Field(default=None, ge=1, le=32)
    max_concurrent_jobs: int | None = Field(default=None, ge=1, le=32)
    expires_at: datetime | None = None
    reason: str = Field(min_length=3, max_length=500)


class ConfirmedAdminAction(BaseModel):
    confirm_email: str = Field(min_length=3, max_length=320)
    reason: str = Field(min_length=3, max_length=500)


class AdminAccountStateChange(ConfirmedAdminAction):
    is_active: bool


class AdminControlClear(ConfirmedAdminAction):
    expected_version: int = Field(ge=0)


def _target(db: Session, user_id: str) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    return user


def _confirm_target(payload: ConfirmedAdminAction, target: User) -> None:
    if normalize_email(payload.confirm_email) != normalize_email(target.email):
        raise HTTPException(status_code=409, detail="Typed confirmation does not match target email")


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _session_payload(row: AuthSession, now: datetime) -> dict:
    expires_at = _aware(row.expires_at)
    active = row.revoked_at is None and expires_at is not None and expires_at > now
    return {
        "id": row.id,
        "active": active,
        "created_at": row.created_at,
        "last_seen_at": row.last_seen_at,
        "expires_at": row.expires_at,
        "revoked_at": row.revoked_at,
    }


def _subscription_payload(row) -> dict | None:
    if row is None:
        return None
    return {
        "id": row.id,
        "plan": row.plan,
        "status": row.status,
        "cancel_at_period_end": row.cancel_at_period_end,
        "current_period_start": row.current_period_start,
        "current_period_end": row.current_period_end,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def _restriction_payload(row: UserRestriction, now: datetime) -> dict:
    expires_at = _aware(row.expires_at)
    effective = bool(row.active) and (expires_at is None or expires_at > now)
    return {
        "id": row.id,
        "case_id": row.case_id,
        "capability": row.capability,
        "reason": row.reason,
        "active": row.active,
        "effective": effective,
        "expires_at": row.expires_at,
        "created_at": row.created_at,
        "revoked_at": row.revoked_at,
    }


@router.get("/search")
def search_users(
    q: str = Query(default="", max_length=200),
    limit: int = Query(default=30, ge=1, le=100),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> list[dict]:
    _ = admin
    stmt = select(User).order_by(User.created_at.desc()).limit(limit)
    term = q.strip()
    if term:
        like = f"%{term}%"
        stmt = stmt.where(or_(User.email.ilike(like), User.display_name.ilike(like), User.id == term))
    rows = list(db.scalars(stmt).all())
    result = []
    for user in rows:
        control = get_control(db, user.id)
        state = control_dict(control)
        result.append({
            "id": user.id,
            "email": user.email,
            "display_name": user.display_name,
            "is_active": user.is_active,
            "is_admin": user.is_admin,
            "has_control_override": control is not None,
            "control_active": bool(state and state["active"]),
            "created_at": user.created_at,
        })
    return result


@router.get("/{user_id}/operations")
def user_operations(
    user_id: str,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    _ = admin
    target = _target(db, user_id)
    quota = get_or_create_quota(db, target, request.app.state.settings)
    subscription = get_subscription(db, target.id)
    control = get_control(db, target.id)
    now = datetime.now(timezone.utc)
    sessions = list(
        db.scalars(
            select(AuthSession)
            .where(AuthSession.user_id == target.id)
            .order_by(AuthSession.last_seen_at.desc(), AuthSession.created_at.desc())
            .limit(20)
        ).all()
    )
    restrictions = list(
        db.scalars(
            select(UserRestriction)
            .where(UserRestriction.user_id == target.id)
            .order_by(UserRestriction.created_at.desc())
            .limit(50)
        ).all()
    )
    audit_rows = list(
        db.scalars(
            select(AdminAuditLog)
            .where(AdminAuditLog.target_type == "user", AdminAuditLog.target_id == target.id)
            .order_by(AdminAuditLog.created_at.desc())
            .limit(30)
        ).all()
    )
    db.commit()
    return {
        "user": {
            "id": target.id,
            "email": target.email,
            "display_name": target.display_name,
            "is_active": target.is_active,
            "is_admin": target.is_admin,
            "created_at": target.created_at,
        },
        "effective_quota": {
            "plan": quota.plan,
            "monthly_compute_seconds_limit": quota.monthly_compute_seconds_limit,
            "max_concurrent_inference": quota.max_concurrent_inference,
            "max_concurrent_jobs": quota.max_concurrent_jobs,
            "updated_at": quota.updated_at,
        },
        "billing_subscription": _subscription_payload(subscription),
        "control": control_dict(control),
        "safety_restrictions": [_restriction_payload(row, now) for row in restrictions],
        "sessions": [_session_payload(row, now) for row in sessions],
        "audit": [
            {
                "id": row.id,
                "actor_user_id": row.actor_user_id,
                "action": row.action,
                "details": row.details,
                "created_at": row.created_at,
            }
            for row in audit_rows
        ],
    }


@router.get("/{user_id}/sessions")
def user_sessions(
    user_id: str,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> list[dict]:
    _ = admin
    target = _target(db, user_id)
    now = datetime.now(timezone.utc)
    rows = db.scalars(
        select(AuthSession)
        .where(AuthSession.user_id == target.id)
        .order_by(AuthSession.last_seen_at.desc(), AuthSession.created_at.desc())
        .limit(100)
    ).all()
    return [_session_payload(row, now) for row in rows]


@router.put("/{user_id}/control")
def replace_user_control(
    user_id: str,
    payload: AdminUserControlPut,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    target = _target(db, user_id)
    try:
        row = put_control(
            db,
            target,
            request.app.state.settings,
            actor_user_id=admin.id,
            expected_version=payload.expected_version,
            plan_override=payload.plan_override,
            monthly_compute_seconds_limit=payload.monthly_compute_seconds_limit,
            max_concurrent_inference=payload.max_concurrent_inference,
            max_concurrent_jobs=payload.max_concurrent_jobs,
            expires_at=payload.expires_at,
            reason=payload.reason,
        )
        quota = get_or_create_quota(db, target, request.app.state.settings)
    except AdminUserControlConflict as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (AdminUserControlError, ValueError) as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    audit(
        db,
        admin,
        "user.control.replace",
        "user",
        target.id,
        {
            "control_version": row.version,
            "plan_override": row.plan_override,
            "expires_at": row.expires_at.isoformat() if row.expires_at else None,
            "reason": row.reason,
        },
    )
    db.commit()
    db.refresh(row)
    return {
        "control": control_dict(row),
        "effective_quota": {
            "plan": quota.plan,
            "monthly_compute_seconds_limit": quota.monthly_compute_seconds_limit,
            "max_concurrent_inference": quota.max_concurrent_inference,
            "max_concurrent_jobs": quota.max_concurrent_jobs,
        },
    }


@router.post("/{user_id}/control/clear")
def remove_user_control(
    user_id: str,
    payload: AdminControlClear,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    target = _target(db, user_id)
    _confirm_target(payload, target)
    previous = control_dict(get_control(db, target.id))
    try:
        clear_control(db, target, expected_version=payload.expected_version)
        quota = get_or_create_quota(db, target, request.app.state.settings)
    except AdminUserControlConflict as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    audit(db, admin, "user.control.clear", "user", target.id, {"reason": payload.reason, "previous": previous})
    db.commit()
    return {
        "control": None,
        "effective_quota": {
            "plan": quota.plan,
            "monthly_compute_seconds_limit": quota.monthly_compute_seconds_limit,
            "max_concurrent_inference": quota.max_concurrent_inference,
            "max_concurrent_jobs": quota.max_concurrent_jobs,
        },
    }


@router.post("/{user_id}/state")
def change_user_state(
    user_id: str,
    payload: AdminAccountStateChange,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    target = _target(db, user_id)
    _confirm_target(payload, target)
    if target.id == admin.id and not payload.is_active:
        raise HTTPException(status_code=409, detail="Administrator cannot suspend own account")
    changed = target.is_active != payload.is_active
    revoked = 0
    target.is_active = payload.is_active
    if not payload.is_active:
        now = datetime.now(timezone.utc)
        sessions = db.scalars(select(AuthSession).where(AuthSession.user_id == target.id, AuthSession.revoked_at.is_(None))).all()
        for session in sessions:
            session.revoked_at = now
            revoked += 1
    audit(db, admin, "user.state.change", "user", target.id, {"is_active": payload.is_active, "revoked_sessions": revoked, "reason": payload.reason})
    db.commit()
    return {"id": target.id, "is_active": target.is_active, "changed": changed, "revoked_sessions": revoked}


@router.post("/{user_id}/sessions/revoke", status_code=status.HTTP_200_OK)
def revoke_user_sessions(
    user_id: str,
    payload: ConfirmedAdminAction,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    target = _target(db, user_id)
    _confirm_target(payload, target)
    if target.id == admin.id:
        raise HTTPException(status_code=409, detail="Use normal logout for the current administrator session")
    now = datetime.now(timezone.utc)
    rows = db.scalars(select(AuthSession).where(AuthSession.user_id == target.id, AuthSession.revoked_at.is_(None))).all()
    count = 0
    for row in rows:
        row.revoked_at = now
        count += 1
    audit(db, admin, "user.sessions.revoke", "user", target.id, {"count": count, "reason": payload.reason})
    db.commit()
    return {"id": target.id, "revoked_sessions": count}
