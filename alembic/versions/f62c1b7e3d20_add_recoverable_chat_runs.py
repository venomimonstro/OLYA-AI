"""add recoverable chat runs

Revision ID: f62c1b7e3d20
Revises: f61b0a4d2c90
Create Date: 2026-09-10
"""

from alembic import op
import sqlalchemy as sa


revision = "f62c1b7e3d20"
down_revision = "f61b0a4d2c90"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "chat_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=True),
        sa.Column("conversation_id", sa.String(length=36), nullable=True),
        sa.Column("client_request_id", sa.String(length=80), nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("runtime_id", sa.String(length=36), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("result_json", sa.JSON(), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=False),
        sa.Column("error_detail", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "client_request_id", name="uq_chat_runs_user_client_request"),
    )
    op.create_index("ix_chat_runs_user_id", "chat_runs", ["user_id"], unique=False)
    op.create_index("ix_chat_runs_project_id", "chat_runs", ["project_id"], unique=False)
    op.create_index("ix_chat_runs_conversation_id", "chat_runs", ["conversation_id"], unique=False)
    op.create_index("ix_chat_runs_status", "chat_runs", ["status"], unique=False)
    op.create_index("ix_chat_runs_created_at", "chat_runs", ["created_at"], unique=False)
    op.create_index("ix_chat_runs_user_updated", "chat_runs", ["user_id", "updated_at"], unique=False)
    op.create_index("ix_chat_runs_status_updated", "chat_runs", ["status", "updated_at"], unique=False)
    op.create_index("ix_chat_runs_conversation", "chat_runs", ["conversation_id", "updated_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_chat_runs_conversation", table_name="chat_runs")
    op.drop_index("ix_chat_runs_status_updated", table_name="chat_runs")
    op.drop_index("ix_chat_runs_user_updated", table_name="chat_runs")
    op.drop_index("ix_chat_runs_created_at", table_name="chat_runs")
    op.drop_index("ix_chat_runs_status", table_name="chat_runs")
    op.drop_index("ix_chat_runs_conversation_id", table_name="chat_runs")
    op.drop_index("ix_chat_runs_project_id", table_name="chat_runs")
    op.drop_index("ix_chat_runs_user_id", table_name="chat_runs")
    op.drop_table("chat_runs")
