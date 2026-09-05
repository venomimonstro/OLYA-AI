from datetime import datetime

from pydantic import BaseModel, Field


class ConversationCreate(BaseModel):
    project_id: str | None = None
    title: str = Field(default="Новый чат", min_length=1, max_length=200)


class ConversationResponse(BaseModel):
    id: str
    project_id: str | None
    title: str
    created_at: datetime
    updated_at: datetime


class MessageResponse(BaseModel):
    id: str
    role: str
    content: str
    created_at: datetime
