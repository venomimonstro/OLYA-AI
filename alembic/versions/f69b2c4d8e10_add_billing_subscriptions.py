"""add billing subscriptions and checkout intents

Revision ID: f69b2c4d8e10
Revises: f62c1b7e3d20
Create Date: 2026-09-12
"""

from alembic import op
import sqlalchemy as sa

revision = "f69b2c4d8e10"
down_revision = "f62c1b7e3d20"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "billing_subscriptions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("plan", sa.String(length=24), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("cancel_at_period_end", sa.Boolean(), nullable=False),
        sa.Column("current_period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("current_period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_payment_record_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["last_payment_record_id"], ["payment_records.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", name="uq_billing_subscription_user"),
    )
    op.create_index("ix_billing_subscriptions_user_id", "billing_subscriptions", ["user_id"], unique=False)
    op.create_index("ix_billing_subscriptions_plan", "billing_subscriptions", ["plan"], unique=False)
    op.create_index("ix_billing_subscriptions_status", "billing_subscriptions", ["status"], unique=False)
    op.create_index("ix_billing_subscriptions_current_period_end", "billing_subscriptions", ["current_period_end"], unique=False)
    op.create_index("ix_billing_subscriptions_last_payment_record_id", "billing_subscriptions", ["last_payment_record_id"], unique=False)
    op.create_index("ix_billing_subscription_status_period", "billing_subscriptions", ["status", "current_period_end"], unique=False)

    op.create_table(
        "billing_checkouts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("plan", sa.String(length=24), nullable=False),
        sa.Column("amount_minor", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("idempotency_key", sa.String(length=80), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payment_record_id", sa.String(length=36), nullable=True),
        sa.Column("refund_record_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["payment_record_id"], ["payment_records.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["refund_record_id"], ["payment_records.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "idempotency_key", name="uq_billing_checkout_user_idempotency"),
    )
    op.create_index("ix_billing_checkouts_user_id", "billing_checkouts", ["user_id"], unique=False)
    op.create_index("ix_billing_checkouts_plan", "billing_checkouts", ["plan"], unique=False)
    op.create_index("ix_billing_checkouts_status", "billing_checkouts", ["status"], unique=False)
    op.create_index("ix_billing_checkouts_expires_at", "billing_checkouts", ["expires_at"], unique=False)
    op.create_index("ix_billing_checkouts_payment_record_id", "billing_checkouts", ["payment_record_id"], unique=False)
    op.create_index("ix_billing_checkouts_refund_record_id", "billing_checkouts", ["refund_record_id"], unique=False)
    op.create_index("ix_billing_checkout_user_created", "billing_checkouts", ["user_id", "created_at"], unique=False)
    op.create_index("ix_billing_checkout_status_expires", "billing_checkouts", ["status", "expires_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_billing_checkout_status_expires", table_name="billing_checkouts")
    op.drop_index("ix_billing_checkout_user_created", table_name="billing_checkouts")
    op.drop_index("ix_billing_checkouts_refund_record_id", table_name="billing_checkouts")
    op.drop_index("ix_billing_checkouts_payment_record_id", table_name="billing_checkouts")
    op.drop_index("ix_billing_checkouts_expires_at", table_name="billing_checkouts")
    op.drop_index("ix_billing_checkouts_status", table_name="billing_checkouts")
    op.drop_index("ix_billing_checkouts_plan", table_name="billing_checkouts")
    op.drop_index("ix_billing_checkouts_user_id", table_name="billing_checkouts")
    op.drop_table("billing_checkouts")
    op.drop_index("ix_billing_subscription_status_period", table_name="billing_subscriptions")
    op.drop_index("ix_billing_subscriptions_last_payment_record_id", table_name="billing_subscriptions")
    op.drop_index("ix_billing_subscriptions_current_period_end", table_name="billing_subscriptions")
    op.drop_index("ix_billing_subscriptions_status", table_name="billing_subscriptions")
    op.drop_index("ix_billing_subscriptions_plan", table_name="billing_subscriptions")
    op.drop_index("ix_billing_subscriptions_user_id", table_name="billing_subscriptions")
    op.drop_table("billing_subscriptions")
