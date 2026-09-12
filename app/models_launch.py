from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return str(uuid4())


class OwnerIntegrationSettings(Base):
    __tablename__ = "owner_integration_settings"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default="global")
    public_base_url: Mapped[str] = mapped_column(String(500), nullable=False, default="")

    smtp_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    smtp_host: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    smtp_port: Mapped[int] = mapped_column(Integer, nullable=False, default=587)
    smtp_username: Mapped[str] = mapped_column(String(320), nullable=False, default="")
    smtp_password_ciphertext: Mapped[str] = mapped_column(Text, nullable=False, default="")
    smtp_from_email: Mapped[str] = mapped_column(String(320), nullable=False, default="")
    smtp_from_name: Mapped[str] = mapped_column(String(160), nullable=False, default="X1 AI")
    smtp_use_tls: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    smtp_use_ssl: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    auth_email_verification_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    metrika_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    metrika_counter_id: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    metrika_webvisor: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    yoomoney_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    yoomoney_receiver: Mapped[str] = mapped_column(String(40), nullable=False, default="")
    yoomoney_notification_secret_ciphertext: Mapped[str] = mapped_column(Text, nullable=False, default="")

    yookassa_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    yookassa_shop_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    yookassa_secret_ciphertext: Mapped[str] = mapped_column(Text, nullable=False, default="")
    payment_default_provider: Mapped[str] = mapped_column(String(24), nullable=False, default="yookassa")

    updated_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)


class UserEmailState(Base):
    __tablename__ = "user_email_states"

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_verification_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_password_reset_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)


class AuthEmailToken(Base):
    __tablename__ = "auth_email_tokens"
    __table_args__ = (
        Index("ix_auth_email_tokens_user_purpose", "user_id", "purpose", "created_at"),
        Index("ix_auth_email_tokens_expiry", "expires_at", "used_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    purpose: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class SupportTicket(Base):
    __tablename__ = "support_tickets"
    __table_args__ = (
        Index("ix_support_tickets_user_updated", "user_id", "updated_at"),
        Index("ix_support_tickets_status_updated", "status", "updated_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    subject: Mapped[str] = mapped_column(String(180), nullable=False)
    category: Mapped[str] = mapped_column(String(32), nullable=False, default="general", index=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="open", index=True)
    priority: Mapped[str] = mapped_column(String(16), nullable=False, default="normal", index=True)
    last_message_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, index=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)


class SupportMessage(Base):
    __tablename__ = "support_messages"
    __table_args__ = (Index("ix_support_messages_ticket_created", "ticket_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    ticket_id: Mapped[str] = mapped_column(ForeignKey("support_tickets.id", ondelete="CASCADE"), nullable=False, index=True)
    author_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    author_role: Mapped[str] = mapped_column(String(16), nullable=False, default="user")
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class PaymentProviderAttempt(Base):
    __tablename__ = "payment_provider_attempts"
    __table_args__ = (
        UniqueConstraint("checkout_id", "provider", name="uq_payment_provider_attempt_checkout_provider"),
        Index("ix_payment_provider_attempt_status", "provider", "status", "updated_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    checkout_id: Mapped[str] = mapped_column(ForeignKey("billing_checkouts.id", ondelete="CASCADE"), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    provider_payment_id: Mapped[str | None] = mapped_column(String(128), nullable=True, unique=True, index=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending", index=True)
    confirmation_url: Mapped[str] = mapped_column(Text, nullable=False, default="")
    metadata_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)
