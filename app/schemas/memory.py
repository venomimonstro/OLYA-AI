from datetime import datetime
from pydantic import BaseModel, Field


class MemoryUpsert(BaseModel):
    key: str = Field(min_length=1, max_length=160)
    value: str = Field(min_length=1, max_length=10_000)


class MemoryResponse(BaseModel):
    id: str
    key: str
    value: str
    source: str
    created_at: datetime
    updated_at: datetime
