from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Conversation, Message, User
from app.schemas.conversations import ConversationCreate, ConversationResponse, MessageResponse
from app.services.access import require_project_role
from app.services.auth import get_current_user

router=APIRouter(prefix="/v1/conversations",tags=["conversations"])

def _private(response:Response)->None:
    response.headers["Cache-Control"]="no-store, private"; response.headers["X-Robots-Tag"]="noindex, nofollow, noarchive, nosnippet"

def _can_read(db:Session,user:User,c:Conversation)->None:
    if c.project_id: require_project_role(db,user,c.project_id,"viewer")
    elif c.owner_id!=user.id: raise HTTPException(status_code=404,detail="Conversation not found")

@router.post("",response_model=ConversationResponse,status_code=status.HTTP_201_CREATED)
def create_conversation(payload:ConversationCreate,response:Response,user:User=Depends(get_current_user),db:Session=Depends(get_db))->ConversationResponse:
    _private(response)
    if payload.project_id: require_project_role(db,user,payload.project_id,"member")
    c=Conversation(owner_id=user.id,project_id=payload.project_id,title=payload.title.strip()); db.add(c); db.commit(); db.refresh(c); return ConversationResponse.model_validate(c,from_attributes=True)

@router.get("",response_model=list[ConversationResponse])
def list_conversations(response:Response,project_id:str|None=None,limit:int=Query(default=50,ge=1,le=100),before:datetime|None=None,user:User=Depends(get_current_user),db:Session=Depends(get_db))->list[ConversationResponse]:
    _private(response)
    if project_id:
        require_project_role(db,user,project_id,"viewer"); stmt=select(Conversation).where(Conversation.project_id==project_id)
    else: stmt=select(Conversation).where(Conversation.owner_id==user.id,Conversation.project_id.is_(None))
    if before is not None: stmt=stmt.where(Conversation.updated_at<before)
    stmt=stmt.order_by(Conversation.updated_at.desc()).limit(limit)
    return [ConversationResponse.model_validate(c,from_attributes=True) for c in db.scalars(stmt).all()]

@router.get("/{conversation_id}/messages",response_model=list[MessageResponse])
def list_messages(conversation_id:str,response:Response,limit:int=Query(default=100,ge=1,le=200),before:datetime|None=None,user:User=Depends(get_current_user),db:Session=Depends(get_db))->list[MessageResponse]:
    _private(response); c=db.get(Conversation,conversation_id)
    if c is None: raise HTTPException(status_code=404,detail="Conversation not found")
    _can_read(db,user,c); stmt=select(Message).where(Message.conversation_id==c.id)
    if before is not None: stmt=stmt.where(Message.created_at<before)
    rows=list(db.scalars(stmt.order_by(Message.created_at.desc()).limit(limit)).all()); rows.reverse()
    return [MessageResponse.model_validate(m,from_attributes=True) for m in rows]
