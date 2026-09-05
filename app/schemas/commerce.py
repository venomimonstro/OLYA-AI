from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class OrganizationCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    slug: str = Field(min_length=2, max_length=120)


class OrganizationRead(BaseModel):
    id: str
    owner_id: str
    name: str
    slug: str
    plan: str
    role: str
    created_at: datetime
    updated_at: datetime


class OrganizationMemberUpsert(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    role: str = Field(pattern="^(member|manager)$")


class OrganizationMemberRead(BaseModel):
    user_id: str
    email: str
    display_name: str
    role: str


class BudgetPut(BaseModel):
    month: str = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
    limit_microunits: int = Field(ge=0)
    alert_percent: int = Field(default=80, ge=1, le=100)
    hard_limit: bool = True


class BudgetRead(BaseModel):
    id: str
    organization_id: str
    month: str
    limit_microunits: int
    alert_percent: int
    hard_limit: bool
    spent_microunits: int
    remaining_microunits: int
    utilization_percent: float


class ApiKeyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    scopes: list[str] = Field(min_length=1, max_length=16)
    organization_id: str | None = None
    rate_limit_per_minute: int | None = Field(default=None, ge=1, le=600)
    expires_at: datetime | None = None


class ApiKeyCreated(BaseModel):
    id: str
    name: str
    prefix: str
    token: str
    scopes: list[str]
    rate_limit_per_minute: int
    organization_id: str | None
    expires_at: datetime | None
    created_at: datetime


class ApiKeyRead(BaseModel):
    id: str
    name: str
    prefix: str
    scopes: list[str]
    rate_limit_per_minute: int
    organization_id: str | None
    status: str
    expires_at: datetime | None
    last_used_at: datetime | None
    created_at: datetime


class ApiContextCreate(BaseModel):
    project_id: str | None = None
    conversation_id: str | None = None
    label: str = Field(default="", max_length=160)
    metadata: dict = Field(default_factory=dict)


class ApiContextRead(BaseModel):
    id: str
    project_id: str | None
    conversation_id: str | None
    label: str
    metadata: dict
    created_at: datetime
    updated_at: datetime


class ApiChatRequest(BaseModel):
    context_id: str | None = None
    project_id: str | None = None
    conversation_id: str | None = None
    task_id: str | None = None
    messages: list[dict]
    mode: str = "auto"
    max_output_tokens: int | None = None
    verification: str = "auto"
    requirements: list[dict] = Field(default_factory=list)
    research_source_ids: list[str] = Field(default_factory=list)
    development_command: str | None = None


class PaymentIngest(BaseModel):
    provider: str = Field(min_length=1, max_length=48)
    provider_event_id: str = Field(min_length=1, max_length=160)
    idempotency_key: str = Field(min_length=1, max_length=160)
    kind: str = Field(default="payment", pattern="^(payment|refund)$")
    amount_minor: int = Field(gt=0)
    currency: str = Field(default="RUB", min_length=3, max_length=3)
    user_id: str | None = None
    organization_id: str | None = None
    metadata: dict = Field(default_factory=dict)


class PaymentRead(BaseModel):
    id: str
    user_id: str | None
    organization_id: str | None
    provider: str
    provider_event_id: str
    idempotency_key: str
    kind: str
    amount_minor: int
    currency: str
    status: str
    created_at: datetime
