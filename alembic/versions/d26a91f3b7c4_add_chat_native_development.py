"""add chat native development session

Revision ID: d26a91f3b7c4
Revises: c25a7b91d4e2
"""
from alembic import op
import sqlalchemy as sa

revision = "d26a91f3b7c4"
down_revision = "c25a7b91d4e2"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "development_chat_sessions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("conversation_id", sa.String(36), sa.ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("plan_id", sa.String(36), sa.ForeignKey("development_plans.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_by", sa.String(36), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="active"),
        sa.Column("current_sprint_id", sa.String(36), sa.ForeignKey("development_sprints.id", ondelete="SET NULL"), nullable=True),
        sa.Column("current_work_item_id", sa.String(36), sa.ForeignKey("development_work_items.id", ondelete="SET NULL"), nullable=True),
        sa.Column("engineering_run_id", sa.String(36), sa.ForeignKey("engineering_runs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("execution_id", sa.String(36), sa.ForeignKey("engineering_executions.id", ondelete="SET NULL"), nullable=True),
        sa.Column("last_action", sa.String(40), nullable=False, server_default="status"),
        sa.Column("last_summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("state_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("conversation_id", name="uq_development_chat_session_conversation"),
    )
    for column in ["project_id", "conversation_id", "plan_id", "created_by", "status", "current_sprint_id", "current_work_item_id", "engineering_run_id", "execution_id"]:
        op.create_index(f"ix_development_chat_sessions_{column}", "development_chat_sessions", [column])
    op.create_index("ix_development_chat_sessions_project_status", "development_chat_sessions", ["project_id", "status"])


def downgrade():
    op.drop_table("development_chat_sessions")
