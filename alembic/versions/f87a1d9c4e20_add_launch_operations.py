"""add launch operations

Revision ID: f87a1d9c4e20
Revises: f72a1c8e5b20
"""
from alembic import op
import sqlalchemy as sa

revision = "f87a1d9c4e20"
down_revision = "f72a1c8e5b20"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "owner_integration_settings",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("public_base_url", sa.String(500), nullable=False, server_default=""),
        sa.Column("smtp_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("smtp_host", sa.String(255), nullable=False, server_default=""),
        sa.Column("smtp_port", sa.Integer(), nullable=False, server_default="587"),
        sa.Column("smtp_username", sa.String(320), nullable=False, server_default=""),
        sa.Column("smtp_password_ciphertext", sa.Text(), nullable=False, server_default=""),
        sa.Column("smtp_from_email", sa.String(320), nullable=False, server_default=""),
        sa.Column("smtp_from_name", sa.String(160), nullable=False, server_default="X1 AI"),
        sa.Column("smtp_use_tls", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("smtp_use_ssl", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("auth_email_verification_required", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("metrika_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("metrika_counter_id", sa.String(32), nullable=False, server_default=""),
        sa.Column("metrika_webvisor", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("yoomoney_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("yoomoney_receiver", sa.String(40), nullable=False, server_default=""),
        sa.Column("yoomoney_notification_secret_ciphertext", sa.Text(), nullable=False, server_default=""),
        sa.Column("yookassa_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("yookassa_shop_id", sa.String(64), nullable=False, server_default=""),
        sa.Column("yookassa_secret_ciphertext", sa.Text(), nullable=False, server_default=""),
        sa.Column("payment_default_provider", sa.String(24), nullable=False, server_default="yookassa"),
        sa.Column("updated_by", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "user_email_states",
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_verification_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_password_reset_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "auth_email_tokens",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("purpose", sa.String(24), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_auth_email_tokens_user_id", "auth_email_tokens", ["user_id"])
    op.create_index("ix_auth_email_tokens_purpose", "auth_email_tokens", ["purpose"])
    op.create_index("ix_auth_email_tokens_token_hash", "auth_email_tokens", ["token_hash"], unique=True)
    op.create_index("ix_auth_email_tokens_expiry", "auth_email_tokens", ["expires_at", "used_at"])
    op.create_index("ix_auth_email_tokens_user_purpose", "auth_email_tokens", ["user_id", "purpose", "created_at"])
    op.create_table(
        "support_tickets",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("subject", sa.String(180), nullable=False),
        sa.Column("category", sa.String(32), nullable=False, server_default="general"),
        sa.Column("status", sa.String(24), nullable=False, server_default="open"),
        sa.Column("priority", sa.String(16), nullable=False, server_default="normal"),
        sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_support_tickets_user_id", "support_tickets", ["user_id"])
    op.create_index("ix_support_tickets_status", "support_tickets", ["status"])
    op.create_index("ix_support_tickets_priority", "support_tickets", ["priority"])
    op.create_index("ix_support_tickets_last_message_at", "support_tickets", ["last_message_at"])
    op.create_index("ix_support_tickets_user_updated", "support_tickets", ["user_id", "updated_at"])
    op.create_index("ix_support_tickets_status_updated", "support_tickets", ["status", "updated_at"])
    op.create_table(
        "support_messages",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("ticket_id", sa.String(36), sa.ForeignKey("support_tickets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("author_user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("author_role", sa.String(16), nullable=False, server_default="user"),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_support_messages_ticket_id", "support_messages", ["ticket_id"])
    op.create_index("ix_support_messages_author_user_id", "support_messages", ["author_user_id"])
    op.create_index("ix_support_messages_ticket_created", "support_messages", ["ticket_id", "created_at"])
    op.create_table(
        "payment_provider_attempts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("checkout_id", sa.String(36), sa.ForeignKey("billing_checkouts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("provider", sa.String(24), nullable=False),
        sa.Column("provider_payment_id", sa.String(128), nullable=True, unique=True),
        sa.Column("status", sa.String(24), nullable=False, server_default="pending"),
        sa.Column("confirmation_url", sa.Text(), nullable=False, server_default=""),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("checkout_id", "provider", name="uq_payment_provider_attempt_checkout_provider"),
    )
    op.create_index("ix_payment_provider_attempts_checkout_id", "payment_provider_attempts", ["checkout_id"])
    op.create_index("ix_payment_provider_attempts_provider", "payment_provider_attempts", ["provider"])
    op.create_index("ix_payment_provider_attempts_provider_payment_id", "payment_provider_attempts", ["provider_payment_id"], unique=True)
    op.create_index("ix_payment_provider_attempts_status", "payment_provider_attempts", ["status"])
    op.create_index("ix_payment_provider_attempt_status", "payment_provider_attempts", ["provider", "status", "updated_at"])


def downgrade() -> None:
    op.drop_table("payment_provider_attempts")
    op.drop_table("support_messages")
    op.drop_table("support_tickets")
    op.drop_table("auth_email_tokens")
    op.drop_table("user_email_states")
    op.drop_table("owner_integration_settings")
