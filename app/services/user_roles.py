from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models_core import User
from app.models_roles import UserRoleAssignment

ASSIGNABLE_ROLES = {"user", "tester"}


def effective_role(db: Session, user: User) -> str:
    if bool(user.is_admin):
        return "admin"
    row = db.scalar(select(UserRoleAssignment).where(UserRoleAssignment.user_id == user.id))
    if row is None or row.role not in ASSIGNABLE_ROLES:
        return "user"
    return row.role


def set_assignable_role(db: Session, user: User, *, role: str, assigned_by: str | None) -> str:
    normalized = str(role or "").strip().lower()
    if normalized not in ASSIGNABLE_ROLES:
        raise ValueError("role must be user or tester")
    if bool(user.is_admin):
        raise ValueError("administrator role is managed separately and cannot be replaced here")
    row = db.scalar(select(UserRoleAssignment).where(UserRoleAssignment.user_id == user.id))
    if normalized == "user":
        if row is not None:
            db.delete(row)
        return "user"
    if row is None:
        row = UserRoleAssignment(user_id=user.id, role="tester", assigned_by=assigned_by)
        db.add(row)
    else:
        row.role = "tester"
        row.assigned_by = assigned_by
    db.flush()
    return "tester"
