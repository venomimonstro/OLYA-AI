from datetime import datetime
from pydantic import BaseModel, Field


class RuntimeCreate(BaseModel):
    workspace_id: str
    cpu_limit: float = Field(default=1.0, gt=0, le=8)
    memory_limit_mb: int = Field(default=1024, ge=256, le=32768)
    disk_limit_mb: int = Field(default=2048, ge=256, le=102400)
    process_limit: int = Field(default=64, ge=8, le=512)
    network_policy: str = Field(default="deny", pattern="^(deny|restricted)$")


class RuntimeRead(BaseModel):
    id: str
    project_id: str
    workspace_id: str
    created_by: str
    status: str
    isolation_backend: str
    network_policy: str
    cpu_limit: float
    memory_limit_mb: int
    disk_limit_mb: int
    process_limit: int
    manifest: dict
    created_at: datetime
    updated_at: datetime
    model_config = {"from_attributes": True}


class RuntimeSecretWrite(BaseModel):
    name: str = Field(min_length=1, max_length=120, pattern=r"^[A-Z][A-Z0-9_]*$")
    value: str = Field(min_length=1, max_length=20000)


class RuntimeSecretRead(BaseModel):
    id: str
    name: str
    created_at: datetime
    updated_at: datetime
    model_config = {"from_attributes": True}


class RuntimeSnapshotRead(BaseModel):
    id: str
    runtime_id: str
    state: str
    manifest_sha256: str
    file_count: int
    total_bytes: int
    manifest: dict
    created_at: datetime
    model_config = {"from_attributes": True}
