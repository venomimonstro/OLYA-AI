from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from app.schemas.projects import ProjectResponse


class ProjectWorkspaceCounts(BaseModel):
    conversations: int
    files: int
    memories: int
    tasks: int


class ProjectConversationPreview(BaseModel):
    id: str
    title: str
    updated_at: datetime


class ProjectFilePreview(BaseModel):
    id: str
    logical_name: str
    status: str
    size_bytes: int
    created_at: datetime


class ProjectMemoryPreview(BaseModel):
    id: str
    key: str
    value: str
    updated_at: datetime


class ProjectTaskPreview(BaseModel):
    id: str
    title: str
    status: str
    current_step: str
    updated_at: datetime


class ProjectDevelopmentPreview(BaseModel):
    id: str
    title: str
    status: str
    current_sprint_ordinal: int | None
    updated_at: datetime


class ProjectWorkspaceResponse(BaseModel):
    project: ProjectResponse
    counts: ProjectWorkspaceCounts
    recent_conversations: list[ProjectConversationPreview]
    recent_files: list[ProjectFilePreview]
    memories: list[ProjectMemoryPreview]
    recent_tasks: list[ProjectTaskPreview]
    development: ProjectDevelopmentPreview | None
