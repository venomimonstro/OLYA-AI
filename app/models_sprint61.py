from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return str(uuid4())


class UserOnboarding(Base):
    """Server-owned first-value state for one account.

    The browser may choose an onboarding path, but milestones are reconciled from
    durable product facts (successful UsageEvent, owned Project and ready
    ProjectFile). This prevents a client click from being treated as activation.
    """

    __tablename__ = "user_onboarding"

    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    first_chat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    first_successful_answer_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    first_project_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    first_file_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    dismissed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reopened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    show_again: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class ProductEvent(Base):
    """Small bounded first-value event ledger used by onboarding and later analytics."""

    __tablename__ = "product_events"
    __table_args__ = (
        Index("ix_product_events_user_created", "user_id", "created_at"),
        Index("ix_product_events_name_created", "event_name", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    project_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="SET NULL"), nullable=True, index=True
    )
    event_name: Mapped[str] = mapped_column(String(64), nullable=False)
    dedupe_key: Mapped[str] = mapped_column(String(180), nullable=False, unique=True, index=True)
    metadata_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, index=True)
