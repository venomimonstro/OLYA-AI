"""add project sandbox and preview session tables

Revision ID: b24f0c91a7d2
Revises: 0a23b4c5d6e7
"""
from alembic import op
import sqlalchemy as sa

revision = "b24f0c91a7d2"
down_revision = "0a23b4c5d6e7"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "project_sandbox_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("runtime_id", sa.String(36), sa.ForeignKey("project_runtimes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("execution_id", sa.String(36), sa.ForeignKey("engineering_executions.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_by", sa.String(36), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("backend", sa.String(32), nullable=False, server_default="unavailable"),
        sa.Column("image", sa.String(255), nullable=False, server_default=""),
        sa.Column("network_policy", sa.String(24), nullable=False, server_default="deny"),
        sa.Column("status", sa.String(24), nullable=False, server_default="planned"),
        sa.Column("commands", sa.JSON(), nullable=False),
        sa.Column("results", sa.JSON(), nullable=False),
        sa.Column("capability_snapshot", sa.JSON(), nullable=False),
        sa.Column("state_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("failure_reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_project_sandbox_runs_project_id", "project_sandbox_runs", ["project_id"])
    op.create_index("ix_project_sandbox_runs_runtime_id", "project_sandbox_runs", ["runtime_id"])
    op.create_index("ix_project_sandbox_runs_execution_id", "project_sandbox_runs", ["execution_id"])
    op.create_index("ix_project_sandbox_runs_created_by", "project_sandbox_runs", ["created_by"])
    op.create_index("ix_project_sandbox_runs_status", "project_sandbox_runs", ["status"])
    op.create_index("ix_project_sandbox_runs_project_status", "project_sandbox_runs", ["project_id", "status"])
    op.create_index("ix_project_sandbox_runs_execution_created", "project_sandbox_runs", ["execution_id", "created_at"])

    op.create_table(
        "project_preview_sessions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("runtime_id", sa.String(36), sa.ForeignKey("project_runtimes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("execution_id", sa.String(36), sa.ForeignKey("engineering_executions.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_by", sa.String(36), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="unavailable"),
        sa.Column("command", sa.JSON(), nullable=False),
        sa.Column("internal_port", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("health_spec", sa.JSON(), nullable=False),
        sa.Column("public_url", sa.Text(), nullable=False, server_default=""),
        sa.Column("container_ref", sa.String(160), nullable=False, server_default=""),
        sa.Column("failure_reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_project_preview_sessions_project_id", "project_preview_sessions", ["project_id"])
    op.create_index("ix_project_preview_sessions_runtime_id", "project_preview_sessions", ["runtime_id"])
    op.create_index("ix_project_preview_sessions_execution_id", "project_preview_sessions", ["execution_id"])
    op.create_index("ix_project_preview_sessions_created_by", "project_preview_sessions", ["created_by"])
    op.create_index("ix_project_preview_sessions_status", "project_preview_sessions", ["status"])
    op.create_index("ix_project_preview_sessions_project_status", "project_preview_sessions", ["project_id", "status"])


def downgrade():
    op.drop_table("project_preview_sessions")
    op.drop_table("project_sandbox_runs")
