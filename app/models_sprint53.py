from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, Index, Integer, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return str(uuid4())


class ComputeBreakdownEvent(Base):
    __tablename__ = "compute_breakdown_events"
    __table_args__ = (
        UniqueConstraint("request_id", name="uq_compute_breakdown_request"),
        Index("ix_compute_breakdown_user_created", "user_id", "created_at"),
        Index("ix_compute_breakdown_mode_created", "mode", "created_at"),
        Index("ix_compute_breakdown_success_created", "success", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    request_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    project_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    conversation_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    mode: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    primary_ms: Mapped[int] = mapped_column(Integer, default=0)
    critic_ms: Mapped[int] = mapped_column(Integer, default=0)
    repair_ms: Mapped[int] = mapped_column(Integer, default=0)
    total_inference_ms: Mapped[int] = mapped_column(Integer, default=0)
    wasted_ms: Mapped[int] = mapped_column(Integer, default=0)
    success: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    verification_extra_inferences: Mapped[int] = mapped_column(Integer, default=0)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
