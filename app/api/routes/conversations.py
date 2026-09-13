from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Conversation, ConversationMemory, Message, User
from app.schemas.conversations import ConversationCreate, ConversationResponse, ConversationUpdate, MessageResponse
from app.services.access import require_project_role
from app.services.auth import get_current_user

router = APIRouter(prefix="/v1/conversations", tags=["conversations"])


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
    return ConversationResponse.model_validate(conversation, from_attributes=True)


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
        # Personal global history includes the user's chats regardless of folder/project.
        stmt = select(Conversation).where(Conversation.owner_id == user.id)
    else:
        stmt = select(Conversation).where(Conversation.owner_id == user.id, Conversation.project_id.is_(None))
    if before is not None:
        stmt = stmt.where(Conversation.updated_at < before)
    stmt = stmt.order_by(Conversation.updated_at.desc()).limit(limit)
    return [ConversationResponse.model_validate(item, from_attributes=True) for item in db.scalars(stmt).all()]


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
    db.commit()
    db.refresh(conversation)
    return ConversationResponse.model_validate(conversation, from_attributes=True)


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
    """Expose remembered facts so memory is never opaque."""
    _private(response)
    conversation = _conversation(db, user, conversation_id)
    stmt = select(ConversationMemory).where(ConversationMemory.conversation_id == conversation.id)
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
    if item is None or item.conversation_id != conversation.id:
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
    db.execute(delete(ConversationMemory).where(ConversationMemory.conversation_id == conversation.id))
    db.commit()
