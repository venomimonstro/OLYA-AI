from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import DateTime, Index, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return str(uuid4())


class AutonomousDevelopmentLedger(Base):
    __tablename__ = "autonomous_development_ledgers"
    __table_args__ = (
        UniqueConstraint("development_session_id", name="uq_autonomous_development_ledger_session"),
        Index("ix_autonomous_development_ledger_project_status", "project_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    development_session_id: Mapped[str] = mapped_column(String(36), index=True)
    project_id: Mapped[str] = mapped_column(String(36), index=True)
    plan_id: Mapped[str] = mapped_column(String(36), index=True)
    immutable_goal: Mapped[str] = mapped_column(Text)
    immutable_constraints: Mapped[dict] = mapped_column(JSON, default=dict)
    contract_sha256: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(24), default="active", index=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    current_state: Mapped[dict] = mapped_column(JSON, default=dict)
    decisions: Mapped[list[dict]] = mapped_column(JSON, default=list)
    completed_work: Mapped[list[dict]] = mapped_column(JSON, default=list)
    pending_work: Mapped[list[dict]] = mapped_column(JSON, default=list)
    failed_work: Mapped[list[dict]] = mapped_column(JSON, default=list)
    checkpoint_ref: Mapped[str] = mapped_column(String(36), default="")
    subagent_budget: Mapped[int] = mapped_column(Integer, default=8)
    max_parallel_subagents: Mapped[int] = mapped_column(Integer, default=1)
    subagent_calls_used: Mapped[int] = mapped_column(Integer, default=0)
    active_subagents: Mapped[int] = mapped_column(Integer, default=0)
    last_heartbeat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class AutonomousDevelopmentCheckpoint(Base):
    __tablename__ = "autonomous_development_checkpoints"
    __table_args__ = (
        UniqueConstraint("ledger_id", "revision", name="uq_autonomous_development_checkpoint_revision"),
        Index("ix_autonomous_development_checkpoint_ledger_created", "ledger_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    ledger_id: Mapped[str] = mapped_column(String(36), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(32), default="state")
    state_sha256: Mapped[str] = mapped_column(String(64), index=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
