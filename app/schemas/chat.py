from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from app.schemas.files import FileCitationRead


ChatMode = Literal["auto", "fast", "work", "deep"]
VerificationMode = Literal["off", "auto", "strict"]
WebMode = Literal["auto", "always", "off"]
RequirementKind = Literal["contains", "not_contains", "max_chars", "min_chars", "valid_json"]
ChatRunState = Literal["running", "succeeded", "failed", "cancelled", "interrupted"]


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str = Field(min_length=1, max_length=200_000)


class AnswerRequirement(BaseModel):
    kind: RequirementKind
    value: str | int | bool | None = None
    label: str = Field(default="", max_length=300)

    @model_validator(mode="after")
    def validate_value(self):
        if self.kind in {"contains", "not_contains"}:
            if not isinstance(self.value, str) or not self.value.strip():
                raise ValueError(f"{self.kind} requires a non-empty string value")
        elif self.kind in {"max_chars", "min_chars"}:
            if not isinstance(self.value, int) or isinstance(self.value, bool) or self.value < 1:
                raise ValueError(f"{self.kind} requires a positive integer value")
        elif self.kind == "valid_json":
            self.value = None
        return self


class ChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1, max_length=200)
    mode: ChatMode = "auto"
    verification: VerificationMode = "auto"
    web_mode: WebMode = "auto"
    requirements: list[AnswerRequirement] = Field(default_factory=list, max_length=30)
    max_output_tokens: int | None = Field(default=None, ge=32, le=8192)
    project_id: str | None = None
    conversation_id: str | None = None
    task_id: str | None = None
    research_source_ids: list[str] = Field(default_factory=list, max_length=10)
    development_command: Literal["auto", "status", "continue", "pause", "resume", "rollback"] | None = None
    # Durable idempotency/reconnect key. Browser clients generate one per
    # logical submission and reuse it only while reconnecting that same run.
    client_request_id: str | None = Field(default=None, min_length=12, max_length=80, pattern=r"^[A-Za-z0-9_.:-]+$")

    @model_validator(mode="after")
    def reject_client_system_messages(self):
        if any(message.role == "system" for message in self.messages):
            raise ValueError("system messages are controlled by X1")
        return self


class ChatUsage(BaseModel):
    raw_message_chars: int
    compiled_message_chars: int
    mode: str
    verification: str = "auto"
    queue_ms: int = 0
    ttft_ms: int | None = None
    output_tokens: int = 0
    tokens_per_second: float | None = None
    verification_risk_score: int = 0
    verification_extra_inferences: int = 0
    critic_used: bool = False
    repair_applied: bool = False


class QualityCheck(BaseModel):
    key: str
    label: str
    status: Literal["passed", "failed", "unverified"]
    detail: str = ""


class QualityReport(BaseModel):
    audit_id: str
    status: Literal["checked", "supported", "unverified", "failed"]
    checks: list[QualityCheck]
    warnings: list[str] = Field(default_factory=list)
    critic: dict[str, Any] | None = None


class ChatResponse(BaseModel):
    text: str
    model: str
    usage: ChatUsage
    quality: QualityReport | None = None
    development: dict[str, Any] | None = None
    # High-level, user-safe record of autonomous work performed for this turn.
    # Never contains hidden reasoning, prompt contents or raw source bodies.
    task_execution: dict[str, Any] | None = None
    conversation_id: str | None = None
    run_id: str | None = None
    client_request_id: str | None = None
    file_citations: list[FileCitationRead] = Field(default_factory=list)


class ChatRunStatus(BaseModel):
    run_id: str
    client_request_id: str
    status: ChatRunState
    conversation_id: str | None = None
    partial_text: str = ""
    result: ChatResponse | None = None
    error_code: str = ""
    error_detail: str = ""
    retryable: bool = False
