from __future__ import annotations

from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import RiskEvent, UserRestriction


def active_restriction(db: Session, user_id: str, capability: str) -> UserRestriction | None:
    now = datetime.now(timezone.utc)
    rows = db.scalars(
        select(UserRestriction).where(
            UserRestriction.user_id == user_id,
            UserRestriction.active.is_(True),
            UserRestriction.capability.in_(["all", capability]),
        )
    ).all()
    for row in rows:
        expires = row.expires_at
        if expires is None:
            return row
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        if expires > now:
            return row
    return None


def require_capability(db: Session, user_id: str, capability: str) -> None:
    row = active_restriction(db, user_id, capability)
    if row is not None:
        raise HTTPException(status_code=403, detail=f"Capability temporarily restricted: {capability}")


def create_risk_event(
    db: Session,
    *,
    user_id: str | None,
    category: str,
    severity: int,
    rule_id: str,
    summary: str,
    evidence: dict | None = None,
    project_id: str | None = None,
    conversation_id: str | None = None,
    message_id: str | None = None,
    detected_by: str = "system",
) -> RiskEvent:
    event = RiskEvent(
        user_id=user_id,
        project_id=project_id,
        conversation_id=conversation_id,
        message_id=message_id,
        category=category,
        severity=max(1, min(5, severity)),
        rule_id=rule_id,
        summary=summary,
        evidence=evidence or {},
        detected_by=detected_by,
    )
    db.add(event)
    db.flush()
    return event
