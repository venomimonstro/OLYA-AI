from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Conversation, Message, User
from app.schemas.conversations import ConversationCreate, ConversationResponse, MessageResponse
from app.services.access import require_project_role
from app.services.auth import get_current_user

router = APIRouter(prefix="/v1/conversations", tags=["conversations"])


def _can_read_conversation(db: Session, user: User, conversation: Conversation) -> None:
    if conversation.project_id:
        require_project_role(db, user, conversation.project_id, "viewer")
    elif conversation.owner_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")


@router.post("", response_model=ConversationResponse, status_code=status.HTTP_201_CREATED)
def create_conversation(payload: ConversationCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> ConversationResponse:
    if payload.project_id:
        require_project_role(db, user, payload.project_id, "member")
    conversation = Conversation(owner_id=user.id, project_id=payload.project_id, title=payload.title.strip())
    db.add(conversation)
    db.commit()
    db.refresh(conversation)
    return ConversationResponse.model_validate(conversation, from_attributes=True)


@router.get("", response_model=list[ConversationResponse])
def list_conversations(project_id: str | None = None, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[ConversationResponse]:
    if project_id:
        require_project_role(db, user, project_id, "viewer")
        stmt = select(Conversation).where(Conversation.project_id == project_id).order_by(Conversation.updated_at.desc())
    else:
        stmt = select(Conversation).where(Conversation.owner_id == user.id, Conversation.project_id.is_(None)).order_by(Conversation.updated_at.desc())
    return [ConversationResponse.model_validate(c, from_attributes=True) for c in db.scalars(stmt).all()]


@router.get("/{conversation_id}/messages", response_model=list[MessageResponse])
def list_messages(conversation_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[MessageResponse]:
    conversation = db.get(Conversation, conversation_id)
    if conversation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")
    _can_read_conversation(db, user, conversation)
    messages = db.scalars(select(Message).where(Message.conversation_id == conversation.id).order_by(Message.created_at)).all()
    return [MessageResponse.model_validate(m, from_attributes=True) for m in messages]
