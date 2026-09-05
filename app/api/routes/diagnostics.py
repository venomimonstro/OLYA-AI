from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Conversation, FrustrationEvent, User
from app.services.auth import get_current_user
from app.services.diagnostics import add_event

router = APIRouter(prefix="/v1/diagnostics", tags=["diagnostics"])
ALLOWED_CLIENT_EVENTS = {"cancelled", "regenerated", "stream_error", "ui_error", "response_not_useful"}

class ClientEventIn(BaseModel):
    kind: str
    conversation_id: str | None = None
    request_id: str | None = None
    metrics: dict = Field(default_factory=dict)

@router.post('/events', status_code=204)
def create_client_event(payload: ClientEventIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> None:
    if payload.kind not in ALLOWED_CLIENT_EVENTS:
        raise HTTPException(status_code=422, detail='Unsupported diagnostic event')
    project_id = None
    if payload.conversation_id:
        conv = db.get(Conversation, payload.conversation_id)
        if conv is None or (conv.owner_id != user.id and conv.project_id is None):
            raise HTTPException(status_code=404, detail='Conversation not found')
        project_id = conv.project_id
    safe_metrics = {k: v for k, v in payload.metrics.items() if k in {'elapsed_ms','ttft_ms','streamed_chars','retry_count','code'}}
    severity = 'critical' if payload.kind in {'stream_error','ui_error'} else 'warning'
    add_event(db, user_id=user.id, project_id=project_id, conversation_id=payload.conversation_id, request_id=payload.request_id,
              kind=payload.kind, severity=severity, source='client', metrics=safe_metrics)
    db.commit()
