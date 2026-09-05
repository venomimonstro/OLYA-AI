from datetime import datetime
from typing import Literal
from pydantic import BaseModel, Field

RoleName = Literal["coordinator", "architect", "developer", "tester", "reviewer"]

class EngineeringRunCreate(BaseModel):
    work_item_id: str
    max_cycles: int = Field(default=2, ge=1, le=4)

class EngineeringRoleTurnRead(BaseModel):
    id: str
    run_id: str
    sequence: int
    cycle: int
    role: str
    status: str
    input_sha256: str
    output: dict
    model_name: str
    inference_ms: int
    created_at: datetime
    model_config = {"from_attributes": True}

class EngineeringRunRead(BaseModel):
    id: str
    project_id: str
    plan_id: str
    sprint_id: str
    work_item_id: str
    task_id: str
    runtime_id: str | None
    workspace_id: str | None
    created_by: str
    status: str
    current_role: str | None
    cycle: int
    max_cycles: int
    state_version: int
    handoff_state: dict
    turns: list[EngineeringRoleTurnRead] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None

class ExecuteRoleRequest(BaseModel):
    expected_version: int

class CoordinatorOutput(BaseModel):
    summary: str = Field(min_length=1, max_length=4000)
    scope_paths: list[str] = Field(default_factory=list, max_length=40)
    risks: list[str] = Field(default_factory=list, max_length=30)
    next_action: str = Field(min_length=1, max_length=1000)

class ArchitectOutput(BaseModel):
    approach: str = Field(min_length=1, max_length=5000)
    files_to_touch: list[str] = Field(default_factory=list, max_length=80)
    constraints: list[str] = Field(default_factory=list, max_length=40)
    decisions_needed: list[str] = Field(default_factory=list, max_length=30)

class DeveloperOutput(BaseModel):
    preview_command: list[str] | None = None
    preview_port: int | None = Field(default=None, ge=1, le=65535)
    preview_health_command: list[str] | None = None
    health_path: str = Field(default="/health", max_length=300)
    implementation_plan: list[str] = Field(min_length=1, max_length=80)
    files_to_change: list[str] = Field(default_factory=list, max_length=80)
    verification_commands: list[list[str]] = Field(default_factory=list, max_length=30)
    notes: list[str] = Field(default_factory=list, max_length=40)

class TesterOutput(BaseModel):
    test_plan: list[str] = Field(min_length=1, max_length=80)
    required_checks: list[str] = Field(default_factory=list, max_length=80)
    risk_cases: list[str] = Field(default_factory=list, max_length=50)

class ReviewerOutput(BaseModel):
    decision: Literal["accept", "revise", "blocked"]
    findings: list[str] = Field(default_factory=list, max_length=80)
    required_changes: list[str] = Field(default_factory=list, max_length=80)
    summary: str = Field(min_length=1, max_length=4000)

class ExecutionCreate(BaseModel):
    engineering_run_id: str
    max_repairs: int = Field(default=1, ge=0, le=2)

class ExecuteApprovedRequest(BaseModel):
    expected_version: int

class FileReplacement(BaseModel):
    path: str
    expected_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    content: str = Field(max_length=500_000)

class ImplementationPatchOutput(BaseModel):
    changes: list[FileReplacement] = Field(min_length=1, max_length=40)
    verification_commands: list[list[str]] = Field(default_factory=list, max_length=20)
    summary: str = Field(min_length=1, max_length=4000)

class EngineeringExecutionEventRead(BaseModel):
    id: str
    execution_id: str
    sequence: int
    kind: str
    status: str
    details: dict
    created_at: datetime
    model_config = {"from_attributes": True}

class EngineeringExecutionRead(BaseModel):
    id: str
    engineering_run_id: str
    project_id: str
    task_id: str
    runtime_id: str
    workspace_id: str
    created_by: str
    status: str
    state_version: int
    snapshot_id: str | None
    attempt: int
    max_repairs: int
    change_manifest: list[dict]
    verification_results: list[dict]
    failure_reason: str
    events: list[EngineeringExecutionEventRead] = Field(default_factory=list)
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class SandboxRunCreate(BaseModel):
    execution_id: str

class SandboxRunExecute(BaseModel):
    expected_version: int

class ProjectSandboxRunRead(BaseModel):
    id: str
    project_id: str
    runtime_id: str
    execution_id: str | None
    created_by: str
    backend: str
    image: str
    network_policy: str
    status: str
    commands: list[list[str]]
    results: list[dict]
    capability_snapshot: dict
    state_version: int
    failure_reason: str
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime
    model_config = {"from_attributes": True}

class PreviewCreate(BaseModel):
    execution_id: str

class ProjectPreviewRead(BaseModel):
    id: str
    project_id: str
    runtime_id: str
    execution_id: str | None
    status: str
    command: list[str]
    internal_port: int
    health_spec: dict
    public_url: str
    failure_reason: str
    expires_at: datetime | None
    created_at: datetime
    updated_at: datetime
    model_config = {"from_attributes": True}
