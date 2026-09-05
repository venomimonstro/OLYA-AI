from datetime import datetime

from pydantic import BaseModel, ConfigDict


class FileRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    logical_name: str
    original_name: str
    version: int
    content_sha256: str
    media_type: str
    size_bytes: int
    status: str
    error_message: str
    is_current: bool
    created_at: datetime


class FileChunkRead(BaseModel):
    id: str
    ordinal: int
    page_number: int | None
    content: str
    score: float | None = None
