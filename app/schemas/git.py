from datetime import datetime
from pydantic import BaseModel, Field


class GitBindingCreate(BaseModel):
    runtime_id: str
    provider: str = Field(default="local", pattern="^(local|github)$")
    repository_url: str = ""
    default_branch: str = Field(default="main", min_length=1, max_length=120, pattern=r"^[A-Za-z0-9._/-]+$")
    mode: str = Field(default="local_only", pattern="^(local_only|direct|branch)$")
    working_branch: str = Field(default="", max_length=160, pattern=r"^$|^[A-Za-z0-9._/-]+$")
    push_enabled: bool = False
    credential_secret_name: str = Field(default="", max_length=120, pattern=r"^$|^[A-Z][A-Z0-9_]*$")


class GitBindingRead(BaseModel):
    id: str
    project_id: str
    runtime_id: str
    workspace_id: str
    provider: str
    repository_url: str
    repository_owner: str
    repository_name: str
    default_branch: str
    mode: str
    working_branch: str
    push_enabled: bool
    credential_secret_name: str
    status: str
    last_local_head: str
    last_remote_head: str
    state_version: int
    created_at: datetime
    updated_at: datetime
    model_config = {"from_attributes": True}


class GitCommitRequest(BaseModel):
    message: str = Field(min_length=3, max_length=300)
    paths: list[str] = Field(default_factory=list, max_length=500)
    expected_head: str = Field(default="", max_length=64)


class GitExternalAction(BaseModel):
    confirm_external_action: bool = False
    expected_head: str = Field(default="", max_length=64)
    expected_remote_head: str = Field(default="", max_length=64)


class GitOperationRead(BaseModel):
    id: str
    binding_id: str
    project_id: str
    created_by: str
    kind: str
    status: str
    branch: str
    head_before: str
    head_after: str
    remote_head: str
    summary: dict
    failure_reason: str
    created_at: datetime
    completed_at: datetime | None
    model_config = {"from_attributes": True}
