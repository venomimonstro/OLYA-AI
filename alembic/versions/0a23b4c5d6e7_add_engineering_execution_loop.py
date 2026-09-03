"""add autonomous engineering execution loop

Revision ID: 0a23b4c5d6e7
Revises: f6b1c83a92d0
"""
from alembic import op
import sqlalchemy as sa

revision = "0a23b4c5d6e7"
down_revision = "f6b1c83a92d0"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "engineering_executions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("engineering_run_id", sa.String(36), sa.ForeignKey("engineering_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("task_id", sa.String(36), sa.ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("runtime_id", sa.String(36), sa.ForeignKey("project_runtimes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("workspace_id", sa.String(36), sa.ForeignKey("code_workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_by", sa.String(36), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("state_version", sa.Integer(), nullable=False),
        sa.Column("snapshot_id", sa.String(36), sa.ForeignKey("project_runtime_snapshots.id", ondelete="SET NULL"), nullable=True),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("max_repairs", sa.Integer(), nullable=False),
        sa.Column("change_manifest", sa.JSON(), nullable=False),
        sa.Column("verification_results", sa.JSON(), nullable=False),
        sa.Column("failure_reason", sa.Text(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("engineering_run_id", name="uq_engineering_execution_run"),
    )
    for col in ["engineering_run_id","project_id","task_id","runtime_id","workspace_id","created_by","status","snapshot_id"]:
        op.create_index(f"ix_engineering_executions_{col}", "engineering_executions", [col])
    op.create_index("ix_engineering_executions_project_status", "engineering_executions", ["project_id", "status"])
    op.create_index("ix_engineering_executions_task_status", "engineering_executions", ["task_id", "status"])

    op.create_table(
        "engineering_execution_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("execution_id", sa.String(36), sa.ForeignKey("engineering_executions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("execution_id", "sequence", name="uq_engineering_execution_event_sequence"),
    )
    for col in ["execution_id","kind","status"]:
        op.create_index(f"ix_engineering_execution_events_{col}", "engineering_execution_events", [col])
    op.create_index("ix_engineering_execution_events_execution_created", "engineering_execution_events", ["execution_id", "created_at"])


def downgrade():
    op.drop_table("engineering_execution_events")
    op.drop_table("engineering_executions")
