"""add code workspace and single agent

Revision ID: d72a9e41bc55
Revises: c4b2e51d9a70
"""
from alembic import op
import sqlalchemy as sa

revision = "d72a9e41bc55"
down_revision = "c4b2e51d9a70"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "code_workspaces",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", sa.String(length=36), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=True),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("root_path", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("file_count", sa.Integer(), nullable=False),
        sa.Column("total_bytes", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_code_workspaces_user_id", "code_workspaces", ["user_id"])
    op.create_index("ix_code_workspaces_project_id", "code_workspaces", ["project_id"])
    op.create_index("ix_code_workspaces_status", "code_workspaces", ["status"])
    op.create_index("ix_code_workspaces_user_created", "code_workspaces", ["user_id", "created_at"])
    op.create_index("ix_code_workspaces_project_created", "code_workspaces", ["project_id", "created_at"])

    op.create_table(
        "code_agent_runs",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("workspace_id", sa.String(length=36), sa.ForeignKey("code_workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("task_id", sa.String(length=36), sa.ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_by", sa.String(length=36), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("goal", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("allowed_paths", sa.JSON(), nullable=False),
        sa.Column("plan", sa.JSON(), nullable=False),
        sa.Column("checkpoint", sa.JSON(), nullable=False),
        sa.Column("command_results", sa.JSON(), nullable=False),
        sa.Column("changed_files", sa.JSON(), nullable=False),
        sa.Column("max_commands", sa.Integer(), nullable=False),
        sa.Column("commands_used", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_code_agent_runs_workspace_id", "code_agent_runs", ["workspace_id"])
    op.create_index("ix_code_agent_runs_task_id", "code_agent_runs", ["task_id"])
    op.create_index("ix_code_agent_runs_created_by", "code_agent_runs", ["created_by"])
    op.create_index("ix_code_agent_runs_status", "code_agent_runs", ["status"])
    op.create_index("ix_code_agent_runs_workspace_created", "code_agent_runs", ["workspace_id", "created_at"])
    op.create_index("ix_code_agent_runs_task_status", "code_agent_runs", ["task_id", "status"])


def downgrade() -> None:
    op.drop_table("code_agent_runs")
    op.drop_table("code_workspaces")
