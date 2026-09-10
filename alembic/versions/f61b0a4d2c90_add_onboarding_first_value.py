"""add onboarding first value state

Revision ID: f61b0a4d2c90
Revises: f60a93c7d511
Create Date: 2026-09-10
"""

from alembic import op
import sqlalchemy as sa


revision = "f61b0a4d2c90"
down_revision = "f60a93c7d511"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_onboarding",
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("first_chat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("first_successful_answer_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("first_project_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("first_file_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dismissed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reopened_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("show_again", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id"),
    )

    op.create_table(
        "product_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=True),
        sa.Column("event_name", sa.String(length=64), nullable=False),
        sa.Column("dedupe_key", sa.String(length=180), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("dedupe_key"),
    )
    op.create_index("ix_product_events_user_id", "product_events", ["user_id"], unique=False)
    op.create_index("ix_product_events_project_id", "product_events", ["project_id"], unique=False)
    op.create_index("ix_product_events_dedupe_key", "product_events", ["dedupe_key"], unique=True)
    op.create_index("ix_product_events_created_at", "product_events", ["created_at"], unique=False)
    op.create_index("ix_product_events_user_created", "product_events", ["user_id", "created_at"], unique=False)
    op.create_index("ix_product_events_name_created", "product_events", ["event_name", "created_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_product_events_name_created", table_name="product_events")
    op.drop_index("ix_product_events_user_created", table_name="product_events")
    op.drop_index("ix_product_events_created_at", table_name="product_events")
    op.drop_index("ix_product_events_dedupe_key", table_name="product_events")
    op.drop_index("ix_product_events_project_id", table_name="product_events")
    op.drop_index("ix_product_events_user_id", table_name="product_events")
    op.drop_table("product_events")
    op.drop_table("user_onboarding")
