from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator


TaskStatus = Literal[
    "created",
    "running",
    "waiting",
    "verifying",
    "completed",
    "failed",
    "cancelled",
]
VerificationMethod = Literal["manual", "evidence"]
EvidenceState = Literal["submitted", "verified", "rejected"]


class CriterionCreate(BaseModel):
    text: str = Field(min_length=1, max_length=1000)
    verification_method: VerificationMethod = "evidence"
    required: bool = True


class TaskCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    goal: str = Field(min_length=1, max_length=10_000)
    constraints: list[str] = Field(default_factory=list, max_length=50)
    criteria: list[CriterionCreate] = Field(min_length=1, max_length=50)
    max_steps: int = Field(default=30, ge=1, le=500)
    max_compute_seconds: int = Field(default=1800, ge=30, le=86_400)

    @model_validator(mode="after")
    def normalize_text(self):
        self.title = self.title.strip()
        self.goal = self.goal.strip()
        self.constraints = [item.strip() for item in self.constraints if item.strip()]
        return self


class CriterionResponse(BaseModel):
    id: str
    text: str
    required: bool
    verification_method: VerificationMethod
    satisfied: bool
    satisfied_at: datetime | None
    verified_evidence_id: str | None


class TaskResponse(BaseModel):
    id: str
    project_id: str
    created_by: str
    title: str
    goal: str
    constraints: list[str]
    status: TaskStatus
    current_step: str
    state_version: int
    completed_steps: int
    max_steps: int
    compute_seconds_used: int
    max_compute_seconds: int
    criteria: list[CriterionResponse]
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None


class TaskPatch(BaseModel):
    expected_version: int = Field(ge=1)
    title: str | None = Field(default=None, min_length=1, max_length=200)
    goal: str | None = Field(default=None, min_length=1, max_length=10_000)
    constraints: list[str] | None = Field(default=None, max_length=50)
    current_step: str | None = Field(default=None, max_length=1000)


class TaskTransition(BaseModel):
    expected_version: int = Field(ge=1)
    current_step: str | None = Field(default=None, max_length=1000)
    reason: str = Field(default="", max_length=1000)


class CriterionManualComplete(BaseModel):
    expected_version: int = Field(ge=1)
    note: str = Field(default="", max_length=2000)


class EvidenceCreate(BaseModel):
    criterion_id: str | None = None
    kind: str = Field(min_length=1, max_length=64)
    source_ref: str = Field(default="", max_length=2000)
    summary: str = Field(min_length=1, max_length=10_000)
    content_sha256: str | None = Field(default=None, min_length=64, max_length=64)


class EvidenceResponse(BaseModel):
    id: str
    task_id: str
    criterion_id: str | None
    kind: str
    source_ref: str
    summary: str
    state: EvidenceState
    created_by: str | None
    verifier: str
    created_at: datetime
    verified_at: datetime | None


class CheckpointCreate(BaseModel):
    expected_version: int = Field(ge=1)
    reason: str = Field(default="manual", max_length=200)
    current_step: str = Field(default="", max_length=1000)
    working_state: dict = Field(default_factory=dict)


class CheckpointResponse(BaseModel):
    id: str
    task_id: str
    sequence: int
    task_state_version: int
    reason: str
    current_step: str
    working_state: dict
    created_at: datetime
