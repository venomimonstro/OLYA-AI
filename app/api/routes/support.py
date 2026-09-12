from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import SupportMessage, SupportTicket, User
from app.services.admin import audit, require_admin
from app.services.auth import get_current_user
from app.services.owner_integrations import send_email, smtp_ready

router = APIRouter(tags=["support"])

CATEGORIES = {"general", "technical", "billing", "account", "quality", "api"}
STATUSES = {"open", "in_progress", "waiting_user", "waiting_admin", "resolved", "closed"}
PRIORITIES = {"low", "normal", "high", "urgent"}
OPEN_STATUSES = {"open", "in_progress", "waiting_user", "waiting_admin"}


class TicketCreate(BaseModel):
    subject: str = Field(min_length=3, max_length=180)
    category: str = Field(default="general", max_length=32)
    message: str = Field(min_length=1, max_length=8000)


class TicketMessageCreate(BaseModel):
    message: str = Field(min_length=1, max_length=8000)


class TicketAdminUpdate(BaseModel):
    status: str | None = Field(default=None, max_length=24)
    priority: str | None = Field(default=None, max_length=16)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _ticket_dict(row: SupportTicket, *, message_count: int | None = None) -> dict:
    return {
        "id": row.id,
        "user_id": row.user_id,
        "subject": row.subject,
        "category": row.category,
        "status": row.status,
        "priority": row.priority,
        "last_message_at": row.last_message_at,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
        "resolved_at": row.resolved_at,
        "message_count": message_count,
    }


def _messages(db: Session, ticket_id: str) -> list[dict]:
    rows = list(db.scalars(select(SupportMessage).where(SupportMessage.ticket_id == ticket_id).order_by(SupportMessage.created_at)).all())
    return [
        {
            "id": row.id,
            "author_user_id": row.author_user_id,
            "author_role": row.author_role,
            "body": row.body,
            "created_at": row.created_at,
        }
        for row in rows
    ]


def _notify_user(db: Session, request: Request, ticket: SupportTicket, subject: str, text: str) -> None:
    user = db.get(User, ticket.user_id)
    if user is None or not smtp_ready(db, request.app.state.settings):
        return
    try:
        send_email(db, request.app.state.settings, to_email=user.email, subject=subject, text=text)
    except Exception:
        # Support must stay available if SMTP is temporarily unhealthy.
        return


@router.post("/v1/support/tickets", status_code=status.HTTP_201_CREATED)
def create_ticket(
    payload: TicketCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    category = payload.category.strip().lower()
    if category not in CATEGORIES:
        raise HTTPException(status_code=422, detail="Unknown support category")
    open_count = int(
        db.scalar(
            select(func.count()).select_from(SupportTicket).where(
                SupportTicket.user_id == user.id,
                SupportTicket.status.in_(tuple(OPEN_STATUSES)),
            )
        )
        or 0
    )
    if open_count >= 3:
        raise HTTPException(status_code=429, detail="Resolve an existing support ticket before opening another one")
    now = _now()
    ticket = SupportTicket(
        user_id=user.id,
        subject=payload.subject.strip(),
        category=category,
        status="open",
        priority="normal",
        last_message_at=now,
    )
    db.add(ticket)
    db.flush()
    db.add(SupportMessage(ticket_id=ticket.id, author_user_id=user.id, author_role="user", body=payload.message.strip()))
    db.commit()
    db.refresh(ticket)
    return {**_ticket_dict(ticket, message_count=1), "messages": _messages(db, ticket.id)}


@router.get("/v1/support/tickets")
def list_my_tickets(
    limit: int = Query(default=50, ge=1, le=100),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[dict]:
    rows = list(
        db.scalars(
            select(SupportTicket)
            .where(SupportTicket.user_id == user.id)
            .order_by(SupportTicket.updated_at.desc())
            .limit(limit)
        ).all()
    )
    return [_ticket_dict(row) for row in rows]


@router.get("/v1/support/tickets/{ticket_id}")
def get_my_ticket(ticket_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    row = db.get(SupportTicket, ticket_id)
    if row is None or row.user_id != user.id:
        raise HTTPException(status_code=404, detail="Support ticket not found")
    messages = _messages(db, row.id)
    return {**_ticket_dict(row, message_count=len(messages)), "messages": messages}


@router.post("/v1/support/tickets/{ticket_id}/messages", status_code=status.HTTP_201_CREATED)
def reply_my_ticket(
    ticket_id: str,
    payload: TicketMessageCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    row = db.get(SupportTicket, ticket_id)
    if row is None or row.user_id != user.id:
        raise HTTPException(status_code=404, detail="Support ticket not found")
    if row.status == "closed":
        raise HTTPException(status_code=409, detail="Closed ticket cannot accept new messages")
    now = _now()
    db.add(SupportMessage(ticket_id=row.id, author_user_id=user.id, author_role="user", body=payload.message.strip()))
    row.status = "waiting_admin"
    row.last_message_at = now
    row.updated_at = now
    row.resolved_at = None
    db.commit()
    messages = _messages(db, row.id)
    return {**_ticket_dict(row, message_count=len(messages)), "messages": messages}


@router.post("/v1/support/tickets/{ticket_id}/close")
def close_my_ticket(ticket_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    row = db.get(SupportTicket, ticket_id)
    if row is None or row.user_id != user.id:
        raise HTTPException(status_code=404, detail="Support ticket not found")
    row.status = "closed"
    row.resolved_at = row.resolved_at or _now()
    row.updated_at = _now()
    db.commit()
    return _ticket_dict(row)


@router.get("/v1/admin/support/tickets")
def admin_list_tickets(
    ticket_status: str | None = Query(default=None, alias="status"),
    category: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> list[dict]:
    _ = admin
    query = select(SupportTicket).order_by(SupportTicket.updated_at.desc())
    if ticket_status:
        if ticket_status not in STATUSES:
            raise HTTPException(status_code=422, detail="Unknown support status")
        query = query.where(SupportTicket.status == ticket_status)
    if category:
        if category not in CATEGORIES:
            raise HTTPException(status_code=422, detail="Unknown support category")
        query = query.where(SupportTicket.category == category)
    rows = list(db.scalars(query.limit(limit)).all())
    users = {row.user_id: db.get(User, row.user_id) for row in rows}
    return [{**_ticket_dict(row), "user_email": users[row.user_id].email if users.get(row.user_id) else None} for row in rows]


@router.get("/v1/admin/support/tickets/{ticket_id}")
def admin_get_ticket(ticket_id: str, admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> dict:
    _ = admin
    row = db.get(SupportTicket, ticket_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Support ticket not found")
    target = db.get(User, row.user_id)
    messages = _messages(db, row.id)
    return {**_ticket_dict(row, message_count=len(messages)), "user_email": target.email if target else None, "messages": messages}


@router.post("/v1/admin/support/tickets/{ticket_id}/messages", status_code=status.HTTP_201_CREATED)
def admin_reply_ticket(
    ticket_id: str,
    payload: TicketMessageCreate,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    row = db.get(SupportTicket, ticket_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Support ticket not found")
    if row.status == "closed":
        raise HTTPException(status_code=409, detail="Closed ticket cannot accept new messages")
    now = _now()
    db.add(SupportMessage(ticket_id=row.id, author_user_id=admin.id, author_role="admin", body=payload.message.strip()))
    row.status = "waiting_user"
    row.last_message_at = now
    row.updated_at = now
    row.resolved_at = None
    audit(db, admin, "support.reply", "support_ticket", row.id, {"status": row.status})
    db.commit()
    _notify_user(db, request, row, f"Ответ поддержки X1: {row.subject}", f"Поддержка X1 ответила на тикет «{row.subject}».\n\n{payload.message.strip()}\n\nОткройте раздел поддержки в X1, чтобы продолжить переписку.")
    messages = _messages(db, row.id)
    return {**_ticket_dict(row, message_count=len(messages)), "messages": messages}


@router.patch("/v1/admin/support/tickets/{ticket_id}")
def admin_update_ticket(
    ticket_id: str,
    payload: TicketAdminUpdate,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    row = db.get(SupportTicket, ticket_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Support ticket not found")
    if payload.status is not None:
        next_status = payload.status.strip().lower()
        if next_status not in STATUSES:
            raise HTTPException(status_code=422, detail="Unknown support status")
        row.status = next_status
        row.resolved_at = _now() if next_status in {"resolved", "closed"} else None
    if payload.priority is not None:
        next_priority = payload.priority.strip().lower()
        if next_priority not in PRIORITIES:
            raise HTTPException(status_code=422, detail="Unknown support priority")
        row.priority = next_priority
    row.updated_at = _now()
    audit(db, admin, "support.update", "support_ticket", row.id, {"status": row.status, "priority": row.priority})
    db.commit()
    if row.status in {"resolved", "closed"}:
        _notify_user(db, request, row, f"Тикет X1: {row.subject}", f"Статус обращения изменён на «{row.status}». Если вопрос остался, откройте новый тикет или ответьте до закрытия обращения.")
    return _ticket_dict(row)


@router.get("/v1/admin/support/summary")
def support_summary(admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> dict:
    _ = admin
    counts = dict(db.execute(select(SupportTicket.status, func.count()).group_by(SupportTicket.status)).all())
    open_total = sum(int(counts.get(name, 0)) for name in OPEN_STATUSES)
    urgent = int(db.scalar(select(func.count()).select_from(SupportTicket).where(SupportTicket.status.in_(tuple(OPEN_STATUSES)), SupportTicket.priority == "urgent")) or 0)
    waiting_admin = int(counts.get("waiting_admin", 0)) + int(counts.get("open", 0))
    return {"open": open_total, "waiting_admin": waiting_admin, "urgent": urgent, "by_status": {str(k): int(v) for k, v in counts.items()}}
