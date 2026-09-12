from __future__ import annotations

import json
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.schemas.chat import AnswerRequirement, ChatMessage


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

    @model_validator(mode="after")
    def validate_context(self):
        if len(json.dumps(self.metadata, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) > 16_384:
            raise ValueError("API context metadata must not exceed 16 KiB")
        return self


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
    messages: list[ChatMessage] = Field(min_length=1, max_length=200)
    mode: Literal["auto", "fast", "work", "deep"] = "auto"
    max_output_tokens: int | None = Field(default=None, ge=32, le=8192)
    verification: Literal["off", "auto", "strict"] = "auto"
    requirements: list[AnswerRequirement] = Field(default_factory=list, max_length=30)
    research_source_ids: list[str] = Field(default_factory=list, max_length=10)
    development_command: Literal["auto", "status", "continue", "pause", "resume", "rollback"] | None = None
    client_request_id: str | None = Field(default=None, min_length=12, max_length=80, pattern=r"^[A-Za-z0-9_.:-]+$")

    @model_validator(mode="after")
    def reject_ambiguous_context(self):
        if self.context_id and (self.project_id or self.conversation_id):
            raise ValueError("context_id cannot be combined with project_id or conversation_id")
        if any(message.role == "system" for message in self.messages):
            raise ValueError("system messages are controlled by X1")
        return self


class BillingCheckoutCreate(BaseModel):
    plan: Literal["x1", "pro", "max", "business"]
    idempotency_key: str = Field(min_length=12, max_length=80, pattern=r"^[A-Za-z0-9_.:-]+$")


class BillingPlanRead(BaseModel):
    name: str
    amount_minor: int
    currency: str
    period_days: int
    purchase_enabled: bool
    monthly_cpu_seconds: int
    resource_budget_microunits: int
    monthly_request_units: int
    daily_request_units: int
    request_unit_weights: dict[str, int]
    max_concurrent_inference: int
    max_concurrent_jobs: int


class BillingCheckoutRead(BaseModel):
    id: str
    user_id: str
    plan: str
    amount_minor: int
    currency: str
    status: str
    idempotency_key: str
    checkout_url: str | None = None
    expires_at: datetime
    payment_record_id: str | None
    created_at: datetime
    updated_at: datetime


class BillingSubscriptionRead(BaseModel):
    id: str
    user_id: str
    plan: str
    status: str
    cancel_at_period_end: bool
    current_period_start: datetime
    current_period_end: datetime
    last_payment_record_id: str | None
    created_at: datetime
    updated_at: datetime


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
