from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint

from app.db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return str(uuid4())


def _declare(name: str, table: str, *items, versioned: bool = False):
    """Declare a readable SQLAlchemy model from columns and table constraints."""
    attrs = {"__tablename__": table}
    table_args = []
    version_column = None
    for item in items:
        if isinstance(item, Column):
            attrs[item.name] = item
            if item.name == "state_version":
                version_column = item
        else:
            table_args.append(item)
    if table_args:
        attrs["__table_args__"] = tuple(table_args)
    if versioned:
        if version_column is None:
            raise ValueError(f"{name} is versioned but has no state_version column")
        attrs["__mapper_args__"] = {"version_id_col": version_column}
    return type(name, (Base,), attrs)


User = _declare(
    "User", "users",
    Column("id", String(36), primary_key=True, default=new_id),
    Column("email", String(320), unique=True, index=True, nullable=False),
    Column("password_hash", String(512), nullable=False),
    Column("display_name", String(120), default="", nullable=False),
    Column("is_active", Boolean, default=True, index=True, nullable=False),
    Column("is_admin", Boolean, default=False, index=True, nullable=False),
    Column("created_at", DateTime(timezone=True), default=utcnow, nullable=False),
)

AuthSession = _declare(
    "AuthSession", "auth_sessions",
    Column("id", String(36), primary_key=True, default=new_id),
    Column("user_id", String(36), ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False),
    Column("token_hash", String(64), unique=True, index=True, nullable=False),
    Column("expires_at", DateTime(timezone=True), index=True, nullable=False),
    Column("created_at", DateTime(timezone=True), default=utcnow, nullable=False),
    Column("last_seen_at", DateTime(timezone=True), default=utcnow, nullable=False),
    Column("revoked_at", DateTime(timezone=True), nullable=True),
)

Project = _declare(
    "Project", "projects",
    Column("id", String(36), primary_key=True, default=new_id),
    Column("owner_id", String(36), ForeignKey("users.id", ondelete="RESTRICT"), index=True, nullable=False),
    Column("name", String(160), nullable=False),
    Column("description", Text, default="", nullable=False),
    Column("instructions", Text, default="", nullable=False),
    Column("created_at", DateTime(timezone=True), default=utcnow, nullable=False),
    Column("updated_at", DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False),
)

ProjectMember = _declare(
    "ProjectMember", "project_members",
    Column("id", String(36), primary_key=True, default=new_id),
    Column("project_id", String(36), ForeignKey("projects.id", ondelete="CASCADE"), index=True, nullable=False),
    Column("user_id", String(36), ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False),
    Column("role", String(24), default="member", nullable=False),
    Column("created_at", DateTime(timezone=True), default=utcnow, nullable=False),
    UniqueConstraint("project_id", "user_id", name="uq_project_member"),
    Index("ix_project_member_user_project", "user_id", "project_id"),
)

ProjectMemory = _declare(
    "ProjectMemory", "project_memories",
    Column("id", String(36), primary_key=True, default=new_id),
    Column("project_id", String(36), ForeignKey("projects.id", ondelete="CASCADE"), index=True, nullable=False),
    Column("key", String(160), nullable=False),
    Column("value", Text, nullable=False),
    Column("source", String(32), default="user", nullable=False),
    Column("created_by", String(36), ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False),
    Column("created_at", DateTime(timezone=True), default=utcnow, nullable=False),
    Column("updated_at", DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False),
    UniqueConstraint("project_id", "key", name="uq_project_memory_key"),
)

ProjectFile = _declare(
    "ProjectFile", "project_files",
    Column("id", String(36), primary_key=True, default=new_id),
    Column("project_id", String(36), ForeignKey("projects.id", ondelete="CASCADE"), index=True, nullable=False),
    Column("uploaded_by", String(36), ForeignKey("users.id", ondelete="RESTRICT"), index=True, nullable=False),
    Column("logical_name", String(240), nullable=False),
    Column("original_name", String(240), nullable=False),
    Column("version", Integer, default=1, nullable=False),
    Column("content_sha256", String(64), nullable=False),
    Column("media_type", String(120), default="application/octet-stream", nullable=False),
    Column("size_bytes", Integer, default=0, nullable=False),
    Column("storage_path", String(1024), nullable=False),
    Column("status", String(24), default="processing", index=True, nullable=False),
    Column("error_message", Text, default="", nullable=False),
    Column("is_current", Boolean, default=True, index=True, nullable=False),
    Column("created_at", DateTime(timezone=True), default=utcnow, nullable=False),
    Index("ix_project_file_project_current", "project_id", "is_current"),
    Index("ix_project_file_project_hash", "project_id", "content_sha256"),
    UniqueConstraint("project_id", "logical_name", "version", name="uq_project_file_version"),
)

FileChunk = _declare(
    "FileChunk", "file_chunks",
    Column("id", String(36), primary_key=True, default=new_id),
    Column("file_id", String(36), ForeignKey("project_files.id", ondelete="CASCADE"), index=True, nullable=False),
    Column("ordinal", Integer, nullable=False),
    Column("page_number", Integer, nullable=True),
    Column("content", Text, nullable=False),
    Column("content_sha256", String(64), index=True, nullable=False),
    Column("char_count", Integer, default=0, nullable=False),
    Column("created_at", DateTime(timezone=True), default=utcnow, nullable=False),
    UniqueConstraint("file_id", "ordinal", name="uq_file_chunk_ordinal"),
    Index("ix_file_chunk_file_ordinal", "file_id", "ordinal"),
)

Task = _declare(
    "Task", "tasks",
    Column("id", String(36), primary_key=True, default=new_id),
    Column("project_id", String(36), ForeignKey("projects.id", ondelete="CASCADE"), index=True, nullable=False),
    Column("created_by", String(36), ForeignKey("users.id", ondelete="RESTRICT"), index=True, nullable=False),
    Column("title", String(200), nullable=False),
    Column("goal", Text, nullable=False),
    Column("constraints", JSON, default=list, nullable=False),
    Column("status", String(24), default="created", index=True, nullable=False),
    Column("current_step", Text, default="", nullable=False),
    Column("state_version", Integer, default=1, nullable=False),
    Column("completed_steps", Integer, default=0, nullable=False),
    Column("max_steps", Integer, default=30, nullable=False),
    Column("compute_seconds_used", Integer, default=0, nullable=False),
    Column("max_compute_seconds", Integer, default=1800, nullable=False),
    Column("created_at", DateTime(timezone=True), default=utcnow, nullable=False),
    Column("updated_at", DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False),
    Column("completed_at", DateTime(timezone=True), nullable=True),
    Index("ix_tasks_project_updated", "project_id", "updated_at"),
    Index("ix_tasks_project_status", "project_id", "status"),
    versioned=True,
)

TaskCriterion = _declare(
    "TaskCriterion", "task_criteria",
    Column("id", String(36), primary_key=True, default=new_id),
    Column("task_id", String(36), ForeignKey("tasks.id", ondelete="CASCADE"), index=True, nullable=False),
    Column("ordinal", Integer, nullable=False),
    Column("text", Text, nullable=False),
    Column("required", Boolean, default=True, nullable=False),
    Column("verification_method", String(16), default="evidence", nullable=False),
    Column("satisfied", Boolean, default=False, index=True, nullable=False),
    Column("satisfied_at", DateTime(timezone=True), nullable=True),
    Column("verified_evidence_id", String(36), nullable=True),
    Column("created_at", DateTime(timezone=True), default=utcnow, nullable=False),
    UniqueConstraint("task_id", "ordinal", name="uq_task_criterion_ordinal"),
    Index("ix_task_criteria_task", "task_id", "ordinal"),
)

TaskEvidence = _declare(
    "TaskEvidence", "task_evidence",
    Column("id", String(36), primary_key=True, default=new_id),
    Column("task_id", String(36), ForeignKey("tasks.id", ondelete="CASCADE"), index=True, nullable=False),
    Column("criterion_id", String(36), ForeignKey("task_criteria.id", ondelete="SET NULL"), nullable=True, index=True),
    Column("kind", String(64), nullable=False),
    Column("source_ref", Text, default="", nullable=False),
    Column("summary", Text, nullable=False),
    Column("content_sha256", String(64), nullable=True, index=True),
    Column("state", String(16), default="submitted", index=True, nullable=False),
    Column("created_by", String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True),
    Column("verifier", String(120), default="", nullable=False),
    Column("created_at", DateTime(timezone=True), default=utcnow, nullable=False),
    Column("verified_at", DateTime(timezone=True), nullable=True),
    Index("ix_task_evidence_task_created", "task_id", "created_at"),
)

TaskCheckpoint = _declare(
    "TaskCheckpoint", "task_checkpoints",
    Column("id", String(36), primary_key=True, default=new_id),
    Column("task_id", String(36), ForeignKey("tasks.id", ondelete="CASCADE"), index=True, nullable=False),
    Column("sequence", Integer, nullable=False),
    Column("task_state_version", Integer, nullable=False),
    Column("reason", String(200), default="manual", nullable=False),
    Column("current_step", Text, default="", nullable=False),
    Column("working_state", JSON, default=dict, nullable=False),
    Column("created_at", DateTime(timezone=True), default=utcnow, nullable=False),
    UniqueConstraint("task_id", "sequence", name="uq_task_checkpoint_sequence"),
    Index("ix_task_checkpoints_task_created", "task_id", "created_at"),
)

Conversation = _declare(
    "Conversation", "conversations",
    Column("id", String(36), primary_key=True, default=new_id),
    Column("owner_id", String(36), ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False),
    Column("project_id", String(36), ForeignKey("projects.id", ondelete="SET NULL"), index=True, nullable=True),
    Column("title", String(200), default="Новый чат", nullable=False),
    Column("created_at", DateTime(timezone=True), default=utcnow, nullable=False),
    Column("updated_at", DateTime(timezone=True), default=utcnow, onupdate=utcnow, index=True, nullable=False),
)

Message = _declare(
    "Message", "messages",
    Column("id", String(36), primary_key=True, default=new_id),
    Column("conversation_id", String(36), ForeignKey("conversations.id", ondelete="CASCADE"), index=True, nullable=False),
    Column("role", String(32), nullable=False),
    Column("content", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), default=utcnow, index=True, nullable=False),
    Index("ix_messages_conversation_created", "conversation_id", "created_at"),
)

UsageEvent = _declare(
    "UsageEvent", "usage_events",
    Column("id", String(36), primary_key=True, default=new_id),
    Column("user_id", String(36), ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False),
    Column("project_id", String(36), ForeignKey("projects.id", ondelete="SET NULL"), index=True, nullable=True),
    Column("conversation_id", String(36), ForeignKey("conversations.id", ondelete="SET NULL"), index=True, nullable=True),
    Column("mode", String(32), nullable=False),
    Column("raw_chars", Integer, default=0, nullable=False),
    Column("compiled_chars", Integer, default=0, nullable=False),
    Column("output_chars", Integer, default=0, nullable=False),
    Column("duration_ms", Integer, default=0, nullable=False),
    Column("inference_ms", Integer, default=0, nullable=False),
    Column("queue_ms", Integer, default=0, nullable=False),
    Column("success", Boolean, default=True, index=True, nullable=False),
    Column("request_id", String(64), nullable=True, index=True),
    Column("created_at", DateTime(timezone=True), default=utcnow, index=True, nullable=False),
)

UserQuota = _declare(
    "UserQuota", "user_quotas",
    Column("user_id", String(36), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    Column("plan", String(32), default="free", nullable=False),
    Column("monthly_compute_seconds_limit", Integer, default=3600, nullable=False),
    Column("max_concurrent_inference", Integer, default=1, nullable=False),
    Column("max_concurrent_jobs", Integer, default=1, nullable=False),
    Column("created_at", DateTime(timezone=True), default=utcnow, nullable=False),
    Column("updated_at", DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False),
)

BackgroundJob = _declare(
    "BackgroundJob", "background_jobs",
    Column("id", String(36), primary_key=True, default=new_id),
    Column("kind", String(120), index=True, nullable=False),
    Column("payload", JSON, default=dict, nullable=False),
    Column("result", JSON, default=dict, nullable=False),
    Column("user_id", String(36), ForeignKey("users.id", ondelete="SET NULL"), index=True, nullable=True),
    Column("project_id", String(36), ForeignKey("projects.id", ondelete="SET NULL"), index=True, nullable=True),
    Column("task_id", String(36), ForeignKey("tasks.id", ondelete="SET NULL"), index=True, nullable=True),
    Column("status", String(24), default="queued", index=True, nullable=False),
    Column("priority", Integer, default=100, nullable=False),
    Column("max_attempts", Integer, default=3, nullable=False),
    Column("attempt_count", Integer, default=0, nullable=False),
    Column("idempotency_key", String(255), unique=True, nullable=True),
    Column("available_at", DateTime(timezone=True), default=utcnow, index=True, nullable=False),
    Column("lease_owner", String(255), nullable=True),
    Column("lease_token", String(255), nullable=True),
    Column("lease_expires_at", DateTime(timezone=True), nullable=True, index=True),
    Column("heartbeat_at", DateTime(timezone=True), nullable=True),
    Column("error_message", Text, default="", nullable=False),
    Column("started_at", DateTime(timezone=True), nullable=True),
    Column("finished_at", DateTime(timezone=True), nullable=True),
    Column("created_at", DateTime(timezone=True), default=utcnow, nullable=False),
    Column("updated_at", DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False),
    Index("ix_background_jobs_ready", "status", "available_at", "priority"),
)
