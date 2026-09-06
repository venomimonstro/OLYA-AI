from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return str(uuid4())


class BetaWave(Base):
    __tablename__ = "beta_waves"
    __table_args__ = (
        UniqueConstraint("cohort", "wave_number", name="uq_beta_wave_cohort_number"),
        Index("ix_beta_wave_cohort_state", "cohort", "state"),
        Index("ix_beta_wave_created", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    cohort: Mapped[str] = mapped_column(String(64), index=True)
    wave_number: Mapped[int] = mapped_column(Integer)
    state: Mapped[str] = mapped_column(String(24), default="planned", index=True)
    target_participants: Mapped[int] = mapped_column(Integer, default=10)
    admitted_count: Mapped[int] = mapped_column(Integer, default=0)
    window_days: Mapped[int] = mapped_column(Integer, default=30)
    compute_budget_seconds: Mapped[int] = mapped_column(Integer, default=0)
    admission_paused: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    pause_reason: Mapped[str] = mapped_column(String(500), default="")
    baseline_metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    latest_metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    decision: Mapped[dict] = mapped_column(JSON, default=dict)
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    observing_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class CapacityPlan(Base):
    __tablename__ = "capacity_plans"
    __table_args__ = (
        UniqueConstraint("version", name="uq_capacity_plan_version"),
        Index("ix_capacity_plan_status_created", "status", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    version: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(24), default="draft", index=True)
    source: Mapped[str] = mapped_column(String(64), default="sprint37-calibration")
    plan: Mapped[dict] = mapped_column(JSON, default=dict)
    signals: Mapped[dict] = mapped_column(JSON, default=dict)
    guardrails: Mapped[dict] = mapped_column(JSON, default=dict)
    comparison: Mapped[dict] = mapped_column(JSON, default=dict)
    previous_plan_id: Mapped[str | None] = mapped_column(ForeignKey("capacity_plans.id", ondelete="SET NULL"), nullable=True, index=True)
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    approved_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    rollback_of_id: Mapped[str | None] = mapped_column(ForeignKey("capacity_plans.id", ondelete="SET NULL"), nullable=True, index=True)
    requires_restart: Mapped[bool] = mapped_column(Boolean, default=True)
    runtime_applied: Mapped[bool] = mapped_column(Boolean, default=False)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rolled_back_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
