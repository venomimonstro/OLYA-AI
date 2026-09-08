from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator


ImageReferenceKind = Literal["edit_source", "identity", "mask"]
ImageEditMode = Literal[
    "auto",
    "remove_object",
    "replace_object",
    "add_object",
    "background",
    "identity_recompose",
]


class ImageReferenceRead(BaseModel):
    id: str
    user_id: str
    project_id: str | None
    blob_id: str
    kind: str
    original_name: str
    status: str
    metadata_json: dict
    created_at: datetime
    deleted_at: datetime | None
    model_config = {"from_attributes": True}


class ImageEditCreate(BaseModel):
    source_reference_id: str
    instruction: str = Field(min_length=2, max_length=6000)
    project_id: str | None = None
    mask_reference_id: str | None = None
    identity_reference_id: str | None = None
    mode: ImageEditMode = "auto"
    preserve_identity: bool = True
    preserve_outside_mask: bool = True
    strict_quality: bool = True
    steps: int | None = Field(default=None, ge=1, le=200)
    seed: int | None = Field(default=None, ge=0, le=2**31 - 1)

    @model_validator(mode="after")
    def validate_edit_contract(self):
        if self.mode == "identity_recompose" and not self.preserve_identity:
            raise ValueError("identity_recompose requires preserve_identity=true")
        return self


class ImageEditRead(BaseModel):
    id: str
    generation_id: str
    user_id: str
    project_id: str | None
    source_reference_id: str
    mask_reference_id: str | None
    identity_reference_id: str | None
    mode: str
    instruction: str
    preserve_identity: bool
    preserve_outside_mask: bool
    strict_quality: bool
    status: str
    plan: dict
    qa_summary: dict
    error_message: str
    created_at: datetime
    updated_at: datetime
    model_config = {"from_attributes": True}


class ImageEditCreateResponse(BaseModel):
    edit: ImageEditRead
    generation_id: str
    job_id: str | None
    status: str
