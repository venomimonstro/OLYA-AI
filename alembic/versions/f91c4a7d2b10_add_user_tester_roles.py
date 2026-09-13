"""add user tester roles

Revision ID: f91c4a7d2b10
Revises: f88b2e7a6c31
"""
from alembic import op
import sqlalchemy as sa

revision = "f91c4a7d2b10"
down_revision = "f88b2e7a6c31"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_role_assignments",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("role", sa.String(24), nullable=False, server_default="user"),
        sa.Column("assigned_by", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_user_role_assignments_user_id", "user_role_assignments", ["user_id"], unique=True)
    op.create_index("ix_user_role_assignments_role", "user_role_assignments", ["role"])


def downgrade() -> None:
    op.drop_index("ix_user_role_assignments_role", table_name="user_role_assignments")
    op.drop_index("ix_user_role_assignments_user_id", table_name="user_role_assignments")
    op.drop_table("user_role_assignments")
