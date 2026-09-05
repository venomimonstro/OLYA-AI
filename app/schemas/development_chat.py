from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

DevelopmentChatCommand = Literal["auto", "status", "continue", "pause", "resume", "rollback"]


class DevelopmentChatRequest(BaseModel):
    project_id: str
    conversation_id: str | None = None
    message: str = Field(min_length=1, max_length=10_000)
    command: DevelopmentChatCommand = "auto"


class DevelopmentChatState(BaseModel):
    session_id: str
    project_id: str
    conversation_id: str
    plan_id: str
    status: str
    plan_status: str
    sprint: dict | None = None
    work_item: dict | None = None
    engineering: dict | None = None
    execution: dict | None = None
    git: dict | None = None
    preview: dict | None = None
    approval_required: dict | None = None
    last_action: str
    last_summary: str
    state_version: int
    created_at: datetime
    updated_at: datetime


class DevelopmentChatResponse(BaseModel):
    text: str
    action: str
    state: DevelopmentChatState
