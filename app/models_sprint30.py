from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return str(uuid4())


class ComplaintCase(Base):
    __tablename__ = "complaint_cases"
    __table_args__ = (
        Index("ix_complaint_cases_fingerprint_status", "fingerprint", "status"),
        Index("ix_complaint_cases_category_created", "category", "created_at"),
        Index("ix_complaint_cases_reporter_created", "reporter_user_id", "created_at"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    reporter_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    project_id: Mapped[str | None] = mapped_column(ForeignKey("projects.id", ondelete="SET NULL"), nullable=True, index=True)
    conversation_id: Mapped[str | None] = mapped_column(ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True, index=True)
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    component: Mapped[str] = mapped_column(String(80), index=True)
    category: Mapped[str] = mapped_column(String(64), index=True)
    severity: Mapped[str] = mapped_column(String(16), default="medium", index=True)
    title: Mapped[str] = mapped_column(String(240))
    expected_behavior: Mapped[str] = mapped_column(Text, default="")
    actual_behavior: Mapped[str] = mapped_column(Text)
    reproduction: Mapped[dict] = mapped_column(JSON, default=dict)
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)
    fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(24), default="new", index=True)
    confirmed_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class RegressionCase(Base):
    __tablename__ = "regression_cases"
    __table_args__ = (
        UniqueConstraint("fingerprint", name="uq_regression_case_fingerprint"),
        Index("ix_regression_cases_blocking_status", "release_blocking", "status"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    source_complaint_id: Mapped[str] = mapped_column(ForeignKey("complaint_cases.id", ondelete="RESTRICT"), index=True)
    fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    component: Mapped[str] = mapped_column(String(80), index=True)
    category: Mapped[str] = mapped_column(String(64), index=True)
    severity: Mapped[str] = mapped_column(String(16), default="medium", index=True)
    title: Mapped[str] = mapped_column(String(240))
    spec: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(24), default="active", index=True)
    confirmed_occurrences: Mapped[int] = mapped_column(Integer, default=1)
    release_blocking: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    last_confirmed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    latest_result: Mapped[str] = mapped_column(String(24), default="not_run", index=True)
    latest_release: Mapped[str] = mapped_column(String(80), default="")
    latest_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class RegressionRun(Base):
    __tablename__ = "regression_runs"
    __table_args__ = (
        Index("ix_regression_runs_case_created", "regression_case_id", "created_at"),
        Index("ix_regression_runs_release_result", "release_version", "result"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    regression_case_id: Mapped[str] = mapped_column(ForeignKey("regression_cases.id", ondelete="CASCADE"), index=True)
    release_version: Mapped[str] = mapped_column(String(80), index=True)
    result: Mapped[str] = mapped_column(String(16), index=True)
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    executed_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class ReleaseGateDecision(Base):
    __tablename__ = "release_gate_decisions"
    __table_args__ = (Index("ix_release_gate_release_created", "release_version", "created_at"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    release_version: Mapped[str] = mapped_column(String(80), index=True)
    channel: Mapped[str] = mapped_column(String(24), default="stable", index=True)
    decision: Mapped[str] = mapped_column(String(16), index=True)
    blocker_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    reasons: Mapped[list[dict]] = mapped_column(JSON, default=list)
    evaluated_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
