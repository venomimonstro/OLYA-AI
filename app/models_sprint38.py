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


class PublicRollout(Base):
    __tablename__ = "public_rollouts"
    __table_args__ = (
        UniqueConstraint("version", name="uq_public_rollout_version"),
        Index("ix_public_rollout_state_created", "state", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    version: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    state: Mapped[str] = mapped_column(String(24), default="planned", index=True)
    exposure_percent: Mapped[int] = mapped_column(Integer, default=0)
    assignment_salt: Mapped[str] = mapped_column(String(64), default=lambda: uuid4().hex)
    baseline_metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    latest_metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    guardrails: Mapped[dict] = mapped_column(JSON, default=dict)
    decision: Mapped[dict] = mapped_column(JSON, default=dict)
    previous_rollout_id: Mapped[str | None] = mapped_column(ForeignKey("public_rollouts.id", ondelete="SET NULL"), nullable=True, index=True)
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    approved_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    paused_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rolled_back_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class MeasuredPlanCatalog(Base):
    __tablename__ = "measured_plan_catalogs"
    __table_args__ = (
        UniqueConstraint("version", name="uq_measured_plan_catalog_version"),
        Index("ix_measured_plan_catalog_status_created", "status", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    version: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(24), default="draft", index=True)
    source: Mapped[str] = mapped_column(String(64), default="closed-beta-measured")
    catalog: Mapped[dict] = mapped_column(JSON, default=dict)
    economics: Mapped[dict] = mapped_column(JSON, default=dict)
    guardrails: Mapped[dict] = mapped_column(JSON, default=dict)
    previous_catalog_id: Mapped[str | None] = mapped_column(ForeignKey("measured_plan_catalogs.id", ondelete="SET NULL"), nullable=True, index=True)
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    approved_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class CircuitBreakerEvent(Base):
    __tablename__ = "circuit_breaker_events"
    __table_args__ = (
        Index("ix_circuit_breaker_status_scope", "status", "scope"),
        Index("ix_circuit_breaker_opened", "opened_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    scope: Mapped[str] = mapped_column(String(64), default="public-launch", index=True)
    kind: Mapped[str] = mapped_column(String(48), index=True)
    status: Mapped[str] = mapped_column(String(24), default="open", index=True)
    automatic: Mapped[bool] = mapped_column(Boolean, default=True)
    reason: Mapped[str] = mapped_column(String(500), default="")
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    opened_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    resolved_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
