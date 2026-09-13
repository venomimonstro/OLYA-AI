from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import User
from app.services.admin import audit, require_admin
from app.services.user_roles import effective_role, set_assignable_role

router = APIRouter(prefix="/v1/admin/testers", tags=["admin-testers"])


class TesterRoleChange(BaseModel):
    role: str = Field(pattern="^(user|tester)$")


@router.get("")
def list_candidates(
    q: str = Query(default="", max_length=200),
    limit: int = Query(default=50, ge=1, le=100),
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
    return [
        {
            "id": user.id,
            "email": user.email,
            "display_name": user.display_name,
            "is_active": user.is_active,
            "is_admin": user.is_admin,
            "role": effective_role(db, user),
            "created_at": user.created_at,
        }
        for user in rows
    ]


@router.put("/{user_id}/role")
def change_role(
    user_id: str,
    payload: TesterRoleChange,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="User not found")
    if target.id == admin.id:
        raise HTTPException(status_code=409, detail="Administrator cannot change own role here")
    previous = effective_role(db, target)
    try:
        current = set_assignable_role(db, target, role=payload.role, assigned_by=admin.id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    audit(
        db,
        admin,
        "user.role.change",
        "user",
        target.id,
        {"previous_role": previous, "role": current},
    )
    db.commit()
    return {"id": target.id, "email": target.email, "role": current}
