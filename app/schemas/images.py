from datetime import datetime
from pydantic import BaseModel, Field


class ImageGenerationCreate(BaseModel):
    prompt: str = Field(min_length=1, max_length=6000)
    negative_prompt: str = Field(default="", max_length=3000)
    project_id: str | None = None
    width: int = Field(default=1024, ge=256, le=4096)
    height: int = Field(default=1024, ge=256, le=4096)
    steps: int | None = Field(default=None, ge=1, le=200)
    seed: int | None = Field(default=None, ge=0, le=2**31 - 1)


class ImageGenerationRead(BaseModel):
    id: str
    user_id: str
    project_id: str | None
    job_id: str | None
    blob_id: str | None
    prompt: str
    negative_prompt: str
    status: str
    backend: str
    model_name: str
    width: int
    height: int
    steps: int
    seed: int
    qa_status: str
    repair_attempts: int
    preferred_blob_id: str | None
    manifest: dict
    error_message: str
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    safety_policy_id: str | None
    safety_status: str
    delivery_status: str
    moderation_note: str
    model_config = {"from_attributes": True}
