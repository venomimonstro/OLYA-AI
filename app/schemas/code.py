from datetime import datetime
from pydantic import BaseModel, Field


class WorkspaceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    project_id: str | None = None


class WorkspaceRead(BaseModel):
    id: str
    user_id: str
    project_id: str | None
    name: str
    status: str
    file_count: int
    total_bytes: int
    created_at: datetime
    updated_at: datetime
    model_config = {"from_attributes": True}


class WorkspaceFileWrite(BaseModel):
    path: str = Field(min_length=1, max_length=500)
    content: str
    expected_sha256: str | None = Field(default=None, min_length=64, max_length=64)


class AgentRunCreate(BaseModel):
    goal: str = Field(min_length=1, max_length=8000)
    task_id: str | None = None
    allowed_paths: list[str] = Field(default_factory=list, max_length=100)
    max_commands: int = Field(default=12, ge=1, le=50)


class AgentRunRead(BaseModel):
    id: str
    workspace_id: str
    task_id: str | None
    created_by: str
    goal: str
    status: str
    allowed_paths: list[str]
    plan: list[dict]
    checkpoint: dict
    command_results: list[dict]
    changed_files: list[dict]
    max_commands: int
    commands_used: int
    created_at: datetime
    updated_at: datetime
    model_config = {"from_attributes": True}


class AgentPlanUpdate(BaseModel):
    plan: list[dict] = Field(max_length=100)
    checkpoint: dict = Field(default_factory=dict)


class AgentFileWrite(BaseModel):
    path: str = Field(min_length=1, max_length=500)
    content: str
    expected_sha256: str | None = Field(default=None, min_length=64, max_length=64)


class AgentCommand(BaseModel):
    argv: list[str] = Field(min_length=1, max_length=24)
    timeout_seconds: int = Field(default=30, ge=1, le=120)
