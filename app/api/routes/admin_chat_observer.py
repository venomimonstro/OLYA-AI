from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Conversation, Message, User
from app.services.admin import require_admin

router = APIRouter(prefix="/v1/admin/chat-observer", tags=["admin-chat-observer"])


def _private(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store, private"
    response.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive, nosnippet"


@router.get("/conversations")
def conversations(
    response: Response,
    q: str = Query(default="", max_length=200),
    user_id: str | None = None,
    limit: int = Query(default=80, ge=1, le=200),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> list[dict]:
    _ = admin
    _private(response)
    stmt = (
        select(Conversation, User)
        .join(User, User.id == Conversation.owner_id)
        .order_by(Conversation.updated_at.desc())
        .limit(limit)
    )
    if user_id:
        stmt = stmt.where(Conversation.owner_id == user_id)
    term = q.strip()
    if term:
        like = f"%{term}%"
        stmt = stmt.where(
            or_(
                Conversation.title.ilike(like),
                User.email.ilike(like),
                User.display_name.ilike(like),
                Conversation.id == term,
            )
        )
    pairs = list(db.execute(stmt).all())
    ids = [conversation.id for conversation, _user in pairs]
    counts: dict[str, int] = {}
    latest: dict[str, Message] = {}
    if ids:
        for conversation_id, count in db.execute(
            select(Message.conversation_id, func.count(Message.id))
            .where(Message.conversation_id.in_(ids))
            .group_by(Message.conversation_id)
        ).all():
            counts[str(conversation_id)] = int(count or 0)
        rows = list(
            db.scalars(
                select(Message)
                .where(Message.conversation_id.in_(ids))
                .order_by(Message.created_at.desc())
            ).all()
        )
        for row in rows:
            latest.setdefault(row.conversation_id, row)
    result = []
    for conversation, user in pairs:
        last = latest.get(conversation.id)
        result.append(
            {
                "id": conversation.id,
                "title": conversation.title,
                "project_id": conversation.project_id,
                "owner_id": conversation.owner_id,
                "email": user.email,
                "display_name": user.display_name,
                "message_count": counts.get(conversation.id, 0),
                "last_role": last.role if last else "",
                "last_message": (last.content[:280] if last else ""),
                "created_at": conversation.created_at,
                "updated_at": conversation.updated_at,
            }
        )
    return result


@router.get("/conversations/{conversation_id}/messages")
def messages(
    conversation_id: str,
    response: Response,
    limit: int = Query(default=200, ge=1, le=500),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    _ = admin
    _private(response)
    conversation = db.get(Conversation, conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Чат не найден")
    user = db.get(User, conversation.owner_id)
    rows = list(
        db.scalars(
            select(Message)
            .where(Message.conversation_id == conversation.id)
            .order_by(Message.created_at.desc())
            .limit(limit)
        ).all()
    )
    rows.reverse()
    return {
        "conversation": {
            "id": conversation.id,
            "title": conversation.title,
            "project_id": conversation.project_id,
            "owner_id": conversation.owner_id,
            "email": user.email if user else "",
            "display_name": user.display_name if user else "",
            "created_at": conversation.created_at,
            "updated_at": conversation.updated_at,
        },
        "messages": [
            {
                "id": row.id,
                "role": row.role,
                "content": row.content,
                "created_at": row.created_at,
            }
            for row in rows
        ],
    }
