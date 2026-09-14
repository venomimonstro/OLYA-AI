from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.chat_management_enhancer import install_chat_management_ui
from app.db import get_db
from app.models import Conversation, ConversationMemory, Message, User
from app.schemas.conversations import ConversationCreate, ConversationResponse, ConversationUpdate, MessageResponse
from app.services.access import require_project_role
from app.services.auth import get_current_user

router = APIRouter(prefix="/v1/conversations", tags=["conversations"])
install_chat_management_ui()

_PIN_KIND = "ui"
_PIN_KEY = "pinned"


class ConversationMemoryResponse(BaseModel):
    id: str
    kind: str
    memory_key: str
    value: str
    source_role: str
    confidence: float
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


def _private(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store, private"
    response.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive, nosnippet"


def _can_read(db: Session, user: User, conversation: Conversation) -> None:
    if conversation.project_id:
        require_project_role(db, user, conversation.project_id, "viewer")
    elif conversation.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Conversation not found")


def _can_write(db: Session, user: User, conversation: Conversation) -> None:
    if conversation.project_id:
        require_project_role(db, user, conversation.project_id, "member")
    elif conversation.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Conversation not found")


def _can_manage(db: Session, user: User, conversation: Conversation) -> None:
    if conversation.owner_id == user.id:
        return
    if conversation.project_id:
        require_project_role(db, user, conversation.project_id, "manager")
        return
    raise HTTPException(status_code=404, detail="Conversation not found")


def _conversation(db: Session, user: User, conversation_id: str, *, write: bool = False) -> Conversation:
    conversation = db.get(Conversation, conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    (_can_write if write else _can_read)(db, user, conversation)
    return conversation


def _pin_map(db: Session, ids: list[str]) -> dict[str, bool]:
    if not ids:
        return {}
    rows = db.execute(
        select(ConversationMemory.conversation_id, ConversationMemory.value).where(
            ConversationMemory.conversation_id.in_(ids),
            ConversationMemory.kind == _PIN_KIND,
            ConversationMemory.memory_key == _PIN_KEY,
        )
    ).all()
    return {str(cid): str(value).strip().lower() in {"1", "true", "yes", "on"} for cid, value in rows}


def _response(conversation: Conversation, pinned: bool = False) -> ConversationResponse:
    return ConversationResponse(
        id=conversation.id,
        project_id=conversation.project_id,
        title=conversation.title,
        pinned=bool(pinned),
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
    )


def _set_pinned(db: Session, conversation: Conversation, pinned: bool) -> None:
    row = db.scalar(
        select(ConversationMemory).where(
            ConversationMemory.conversation_id == conversation.id,
            ConversationMemory.kind == _PIN_KIND,
            ConversationMemory.memory_key == _PIN_KEY,
        )
    )
    if pinned:
        if row is None:
            db.add(
                ConversationMemory(
                    conversation_id=conversation.id,
                    project_id=conversation.project_id,
                    kind=_PIN_KIND,
                    memory_key=_PIN_KEY,
                    value="1",
                    keywords=["pinned"],
                    source_role="system",
                    confidence=1.0,
                )
            )
        else:
            row.value = "1"
            row.project_id = conversation.project_id
    elif row is not None:
        db.delete(row)


@router.post("", response_model=ConversationResponse, status_code=status.HTTP_201_CREATED)
def create_conversation(
    payload: ConversationCreate,
    response: Response,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ConversationResponse:
    _private(response)
    if payload.project_id:
        require_project_role(db, user, payload.project_id, "member")
    conversation = Conversation(owner_id=user.id, project_id=payload.project_id, title=payload.title.strip())
    db.add(conversation)
    db.commit()
    db.refresh(conversation)
    return _response(conversation, False)


@router.get("", response_model=list[ConversationResponse])
def list_conversations(
    response: Response,
    project_id: str | None = None,
    all_projects: bool = False,
    limit: int = Query(default=50, ge=1, le=100),
    before: datetime | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[ConversationResponse]:
    _private(response)
    if project_id:
        require_project_role(db, user, project_id, "viewer")
        stmt = select(Conversation).where(Conversation.project_id == project_id)
    elif all_projects:
        stmt = select(Conversation).where(Conversation.owner_id == user.id)
    else:
        stmt = select(Conversation).where(Conversation.owner_id == user.id, Conversation.project_id.is_(None))
    if before is not None:
        stmt = stmt.where(Conversation.updated_at < before)
    rows = list(db.scalars(stmt.order_by(Conversation.updated_at.desc()).limit(limit)).all())
    pins = _pin_map(db, [row.id for row in rows])
    rows.sort(key=lambda row: (not pins.get(row.id, False), -row.updated_at.timestamp()))
    return [_response(item, pins.get(item.id, False)) for item in rows]


@router.patch("/{conversation_id}", response_model=ConversationResponse)
def update_conversation(
    conversation_id: str,
    payload: ConversationUpdate,
    response: Response,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ConversationResponse:
    _private(response)
    conversation = _conversation(db, user, conversation_id, write=True)
    changes = payload.model_dump(exclude_unset=True)
    if "title" in changes:
        title = (changes["title"] or "").strip()
        if not title:
            raise HTTPException(status_code=422, detail="Conversation title cannot be empty")
        conversation.title = title
    if "project_id" in changes:
        target_project_id = changes["project_id"] or None
        if target_project_id != conversation.project_id:
            if conversation.owner_id != user.id:
                raise HTTPException(status_code=403, detail="Only the conversation owner can move it between projects")
            if target_project_id:
                require_project_role(db, user, target_project_id, "member")
            conversation.project_id = target_project_id
    if "pinned" in changes and changes["pinned"] is not None:
        _set_pinned(db, conversation, bool(changes["pinned"]))
    db.commit()
    db.refresh(conversation)
    pinned = bool(_pin_map(db, [conversation.id]).get(conversation.id, False))
    return _response(conversation, pinned)


@router.delete("/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_conversation(
    conversation_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    conversation = db.get(Conversation, conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    _can_manage(db, user, conversation)
    db.delete(conversation)
    db.commit()


@router.get("/{conversation_id}/messages", response_model=list[MessageResponse])
def list_messages(
    conversation_id: str,
    response: Response,
    limit: int = Query(default=100, ge=1, le=200),
    before: datetime | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[MessageResponse]:
    _private(response)
    conversation = _conversation(db, user, conversation_id)
    stmt = select(Message).where(Message.conversation_id == conversation.id)
    if before is not None:
        stmt = stmt.where(Message.created_at < before)
    rows = list(db.scalars(stmt.order_by(Message.created_at.desc()).limit(limit)).all())
    rows.reverse()
    return [MessageResponse.model_validate(item, from_attributes=True) for item in rows]


@router.get("/{conversation_id}/memory", response_model=list[ConversationMemoryResponse])
def list_conversation_memory(
    conversation_id: str,
    response: Response,
    include_summary: bool = False,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[ConversationMemoryResponse]:
    _private(response)
    conversation = _conversation(db, user, conversation_id)
    stmt = select(ConversationMemory).where(
        ConversationMemory.conversation_id == conversation.id,
        ConversationMemory.kind != _PIN_KIND,
    )
    if not include_summary:
        stmt = stmt.where(ConversationMemory.kind != "summary")
    rows = db.scalars(stmt.order_by(ConversationMemory.updated_at.desc()).limit(250)).all()
    return [ConversationMemoryResponse.model_validate(item, from_attributes=True) for item in rows]


@router.delete("/{conversation_id}/memory/{memory_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_conversation_memory(
    conversation_id: str,
    memory_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    conversation = _conversation(db, user, conversation_id, write=True)
    item = db.get(ConversationMemory, memory_id)
    if item is None or item.conversation_id != conversation.id or item.kind == _PIN_KIND:
        raise HTTPException(status_code=404, detail="Conversation memory not found")
    db.delete(item)
    db.commit()


@router.delete("/{conversation_id}/memory", status_code=status.HTTP_204_NO_CONTENT)
def clear_conversation_memory(
    conversation_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    conversation = _conversation(db, user, conversation_id, write=True)
    db.execute(
        delete(ConversationMemory).where(
            ConversationMemory.conversation_id == conversation.id,
            ConversationMemory.kind != _PIN_KIND,
        )
    )
    db.commit()
