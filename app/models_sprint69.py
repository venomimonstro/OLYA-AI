from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return str(uuid4())


class BillingSubscription(Base):
    __tablename__ = "billing_subscriptions"
    __table_args__ = (
        UniqueConstraint("user_id", name="uq_billing_subscription_user"),
        Index("ix_billing_subscription_status_period", "status", "current_period_end"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    plan: Mapped[str] = mapped_column(String(24), nullable=False, default="free", index=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="active", index=True)
    cancel_at_period_end: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    current_period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    current_period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    last_payment_record_id: Mapped[str | None] = mapped_column(ForeignKey("payment_records.id", ondelete="SET NULL"), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)


class BillingCheckout(Base):
    __tablename__ = "billing_checkouts"
    __table_args__ = (
        UniqueConstraint("user_id", "idempotency_key", name="uq_billing_checkout_user_idempotency"),
        Index("ix_billing_checkout_user_created", "user_id", "created_at"),
        Index("ix_billing_checkout_status_expires", "status", "expires_at"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    plan: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    amount_minor: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="RUB")
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending", index=True)
    idempotency_key: Mapped[str] = mapped_column(String(80), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    payment_record_id: Mapped[str | None] = mapped_column(ForeignKey("payment_records.id", ondelete="SET NULL"), nullable=True, index=True)
    refund_record_id: Mapped[str | None] = mapped_column(ForeignKey("payment_records.id", ondelete="SET NULL"), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)
