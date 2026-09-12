"""add admin user controls

Revision ID: f72a1c8e5b20
Revises: f69b2c4d8e10
Create Date: 2026-09-12
"""

from alembic import op
import sqlalchemy as sa

revision = "f72a1c8e5b20"
down_revision = "f69b2c4d8e10"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "admin_user_controls",
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("plan_override", sa.String(length=24), nullable=True),
        sa.Column("monthly_compute_seconds_limit", sa.Integer(), nullable=True),
        sa.Column("max_concurrent_inference", sa.Integer(), nullable=True),
        sa.Column("max_concurrent_jobs", sa.Integer(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reason", sa.String(length=500), nullable=False),
        sa.Column("updated_by", sa.String(length=36), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("user_id"),
    )
    op.create_index("ix_admin_user_controls_expires_at", "admin_user_controls", ["expires_at"], unique=False)
    op.create_index("ix_admin_user_controls_updated_by", "admin_user_controls", ["updated_by"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_admin_user_controls_updated_by", table_name="admin_user_controls")
    op.drop_index("ix_admin_user_controls_expires_at", table_name="admin_user_controls")
    op.drop_table("admin_user_controls")
