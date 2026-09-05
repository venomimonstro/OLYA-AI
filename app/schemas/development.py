from __future__ import annotations

from datetime import datetime
import json
from typing import Literal

from pydantic import BaseModel, Field, model_validator

PlanStatus = Literal["draft", "active", "paused", "completed"]
SprintStatus = Literal["planned", "active", "verifying", "completed", "blocked"]
WorkItemKind = Literal["feature", "bugfix", "test", "migration", "docs", "infra", "research"]
WorkItemStatus = Literal["planned", "active", "completed", "blocked"]


class RequirementInput(BaseModel):
    key: str = Field(min_length=1, max_length=120)
    text: str = Field(min_length=1, max_length=4000)
    priority: Literal["must", "should", "could"] = "must"


class WorkItemInput(BaseModel):
    ordinal: int = Field(ge=1, le=1000)
    title: str = Field(min_length=1, max_length=200)
    goal: str = Field(min_length=1, max_length=8000)
    kind: WorkItemKind = "feature"
    dependencies: list[int] = Field(default_factory=list, max_length=50)
    acceptance_criteria: list[str] = Field(min_length=1, max_length=50)


class SprintInput(BaseModel):
    ordinal: int = Field(ge=1, le=500)
    title: str = Field(min_length=1, max_length=200)
    goal: str = Field(min_length=1, max_length=10_000)
    dependencies: list[int] = Field(default_factory=list, max_length=50)
    acceptance_criteria: list[str] = Field(min_length=1, max_length=50)
    items: list[WorkItemInput] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def validate_items(self):
        ordinals = [x.ordinal for x in self.items]
        if len(ordinals) != len(set(ordinals)):
            raise ValueError("Work item ordinals must be unique within a sprint")
        known = set(ordinals)
        for item in self.items:
            if item.ordinal in item.dependencies or any(dep not in known for dep in item.dependencies):
                raise ValueError("Work item dependencies must reference other items in the same sprint")
        return self


class DevelopmentPlanCreate(BaseModel):
    project_id: str
    runtime_id: str | None = None
    title: str = Field(min_length=1, max_length=200)
    product_brief: str = Field(min_length=1, max_length=30_000)
    requirements: list[RequirementInput] = Field(min_length=1, max_length=200)
    architecture: dict = Field(default_factory=dict)
    constraints: list[str] = Field(default_factory=list, max_length=100)
    sprints: list[SprintInput] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def validate_plan(self):
        sprint_ordinals = [x.ordinal for x in self.sprints]
        if len(sprint_ordinals) != len(set(sprint_ordinals)):
            raise ValueError("Sprint ordinals must be unique")
        known = set(sprint_ordinals)
        req_keys = [r.key.strip().lower() for r in self.requirements]
        if len(req_keys) != len(set(req_keys)):
            raise ValueError("Requirement keys must be unique")
        for sprint in self.sprints:
            if sprint.ordinal in sprint.dependencies or any(dep not in known for dep in sprint.dependencies):
                raise ValueError("Sprint dependencies must reference sprints in this plan")
            if any(dep >= sprint.ordinal for dep in sprint.dependencies):
                raise ValueError("Sprint dependencies must point to earlier sprints")
        self.constraints = [x.strip() for x in self.constraints if x.strip()]
        if len(json.dumps(self.architecture, ensure_ascii=False)) > 50_000:
            raise ValueError("Architecture payload is too large")
        return self


class DevelopmentPlanPatch(BaseModel):
    expected_version: int = Field(ge=1)
    product_brief: str | None = Field(default=None, min_length=1, max_length=30_000)
    requirements: list[RequirementInput] | None = Field(default=None, min_length=1, max_length=200)
    architecture: dict | None = None
    constraints: list[str] | None = Field(default=None, max_length=100)
    status: Literal["draft", "active", "paused"] | None = None


class DecisionCreate(BaseModel):
    expected_version: int = Field(ge=1)
    key: str = Field(min_length=1, max_length=120)
    title: str = Field(min_length=1, max_length=200)
    decision: str = Field(min_length=1, max_length=10_000)
    rationale: str = Field(default="", max_length=10_000)


class DecisionRead(BaseModel):
    id: str
    key: str
    title: str
    decision: str
    rationale: str
    status: str
    created_by: str
    created_at: datetime
    updated_at: datetime


class WorkItemRead(BaseModel):
    id: str
    ordinal: int
    title: str
    goal: str
    kind: str
    status: str
    dependencies: list[int]
    acceptance_criteria: list[str]
    task_id: str | None


class SprintRead(BaseModel):
    id: str
    ordinal: int
    title: str
    goal: str
    status: str
    dependencies: list[int]
    acceptance_criteria: list[str]
    items: list[WorkItemRead]
    started_at: datetime | None
    completed_at: datetime | None


class DevelopmentPlanRead(BaseModel):
    id: str
    project_id: str
    runtime_id: str | None
    created_by: str
    title: str
    product_brief: str
    requirements: list[dict]
    architecture: dict
    constraints: list[str]
    status: str
    current_sprint_ordinal: int | None
    state_version: int
    sprints: list[SprintRead]
    decisions: list[DecisionRead]
    created_at: datetime
    updated_at: datetime


class SprintActivate(BaseModel):
    expected_version: int = Field(ge=1)


class CheckpointCreate(BaseModel):
    expected_version: int = Field(ge=1)
    reason: str = Field(default="manual", max_length=200)
    runtime_snapshot_id: str | None = None


class DevelopmentCheckpointRead(BaseModel):
    id: str
    sequence: int
    plan_state_version: int
    current_sprint_ordinal: int | None
    runtime_snapshot_id: str | None
    state_sha256: str
    state: dict
    reason: str
    created_at: datetime


class ArchitectDraftRequest(BaseModel):
    project_id: str
    runtime_id: str | None = None
    product_brief: str = Field(min_length=20, max_length=30_000)
    constraints: list[str] = Field(default_factory=list, max_length=100)
    target_sprints: int = Field(default=6, ge=1, le=30)


class ArchitectDraftResponse(BaseModel):
    plan: DevelopmentPlanRead
    model: str
    inference_ms: int

class FutureReplanRequest(BaseModel):
    expected_version: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=1000)
    future_sprints: list[SprintInput] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def validate_future(self):
        ordinals = [x.ordinal for x in self.future_sprints]
        if len(ordinals) != len(set(ordinals)):
            raise ValueError("Future sprint ordinals must be unique")
        return self
