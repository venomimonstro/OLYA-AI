"""add admin control plane

Revision ID: 4ac91e0f77d2
Revises: 9f6b3d21a4c8
Create Date: 2026-09-01
"""
from alembic import op
import sqlalchemy as sa

revision = "4ac91e0f77d2"
down_revision = "9f6b3d21a4c8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("is_admin", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch.create_index("ix_users_is_admin", ["is_admin"])
    op.create_table(
        "admin_audit_logs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("actor_user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("action", sa.String(120), nullable=False),
        sa.Column("target_type", sa.String(64), nullable=False, server_default=""),
        sa.Column("target_id", sa.String(120), nullable=False, server_default=""),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_admin_audit_logs_actor_user_id", "admin_audit_logs", ["actor_user_id"])
    op.create_index("ix_admin_audit_logs_action", "admin_audit_logs", ["action"])
    op.create_index("ix_admin_audit_logs_created_at", "admin_audit_logs", ["created_at"])
    op.create_index("ix_admin_audit_created", "admin_audit_logs", ["created_at"])
    op.create_index("ix_admin_audit_actor", "admin_audit_logs", ["actor_user_id", "created_at"])
    op.create_table(
        "system_settings",
        sa.Column("key", sa.String(120), primary_key=True),
        sa.Column("value", sa.JSON(), nullable=False),
        sa.Column("updated_by", sa.String(36), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_system_settings_updated_by", "system_settings", ["updated_by"])


def downgrade() -> None:
    op.drop_index("ix_system_settings_updated_by", table_name="system_settings")
    op.drop_table("system_settings")
    op.drop_index("ix_admin_audit_actor", table_name="admin_audit_logs")
    op.drop_index("ix_admin_audit_created", table_name="admin_audit_logs")
    op.drop_index("ix_admin_audit_logs_created_at", table_name="admin_audit_logs")
    op.drop_index("ix_admin_audit_logs_action", table_name="admin_audit_logs")
    op.drop_index("ix_admin_audit_logs_actor_user_id", table_name="admin_audit_logs")
    op.drop_table("admin_audit_logs")
    with op.batch_alter_table("users") as batch:
        batch.drop_index("ix_users_is_admin")
        batch.drop_column("is_admin")
