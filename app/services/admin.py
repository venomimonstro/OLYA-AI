from __future__ import annotations
from fastapi import Depends, HTTPException, status
from sqlalchemy.orm import Session
from app.db import get_db
from app.models import AdminAuditLog, User
from app.services.auth import get_current_user


def require_admin(user: User = Depends(get_current_user)) -> User:
    if not user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Administrator access required")
    return user


def audit(db: Session, actor: User, action: str, target_type: str = "", target_id: str = "", details: dict | None = None) -> None:
    db.add(AdminAuditLog(actor_user_id=actor.id, action=action, target_type=target_type, target_id=target_id, details=details or {}))
