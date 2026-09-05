from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class DocumentBlock(BaseModel):
    type: Literal["heading", "paragraph", "bullet_list", "numbered_list", "table", "page_break"]
    text: str = ""
    level: int = Field(default=1, ge=1, le=4)
    items: list[str] = Field(default_factory=list)
    rows: list[list[str]] = Field(default_factory=list)
    header: bool = True


class DocumentSpec(BaseModel):
    title: str = Field(min_length=1, max_length=240)
    logical_name: str = Field(default="document.docx", min_length=1, max_length=240)
    project_id: str | None = None
    blocks: list[DocumentBlock] = Field(default_factory=list, max_length=500)


class DocumentArtifactRead(BaseModel):
    id: str
    user_id: str
    project_id: str | None
    title: str
    logical_name: str
    status: str
    current_revision: int
    released_revision: int | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class DocumentRevisionRead(BaseModel):
    id: str
    artifact_id: str
    revision: int
    docx_sha256: str
    pdf_sha256: str
    page_count: int
    qa_status: str
    qa_report: dict
    created_at: datetime

    model_config = {"from_attributes": True}
