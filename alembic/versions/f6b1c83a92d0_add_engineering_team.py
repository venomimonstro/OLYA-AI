"""add multi-agent engineering team

Revision ID: f6b1c83a92d0
Revises: e5c21a7d4b90
"""
from alembic import op
import sqlalchemy as sa

revision = "f6b1c83a92d0"
down_revision = "e5c21a7d4b90"
branch_labels = None
depends_on = None

def upgrade():
    op.create_table(
        "engineering_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("plan_id", sa.String(36), sa.ForeignKey("development_plans.id", ondelete="CASCADE"), nullable=False),
        sa.Column("sprint_id", sa.String(36), sa.ForeignKey("development_sprints.id", ondelete="CASCADE"), nullable=False),
        sa.Column("work_item_id", sa.String(36), sa.ForeignKey("development_work_items.id", ondelete="CASCADE"), nullable=False),
        sa.Column("task_id", sa.String(36), sa.ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("runtime_id", sa.String(36), sa.ForeignKey("project_runtimes.id", ondelete="SET NULL"), nullable=True),
        sa.Column("workspace_id", sa.String(36), sa.ForeignKey("code_workspaces.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_by", sa.String(36), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("current_role", sa.String(24), nullable=True),
        sa.Column("cycle", sa.Integer(), nullable=False),
        sa.Column("max_cycles", sa.Integer(), nullable=False),
        sa.Column("state_version", sa.Integer(), nullable=False),
        sa.Column("handoff_state", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("work_item_id", name="uq_engineering_run_work_item"),
    )
    for col in ["project_id","plan_id","sprint_id","work_item_id","task_id","runtime_id","workspace_id","created_by","status","current_role"]:
        op.create_index(f"ix_engineering_runs_{col}", "engineering_runs", [col])
    op.create_index("ix_engineering_runs_project_status", "engineering_runs", ["project_id", "status"])
    op.create_index("ix_engineering_runs_task_status", "engineering_runs", ["task_id", "status"])
    op.create_table(
        "engineering_role_turns",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("engineering_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("cycle", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(24), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("input_sha256", sa.String(64), nullable=False),
        sa.Column("output", sa.JSON(), nullable=False),
        sa.Column("model_name", sa.String(160), nullable=False),
        sa.Column("inference_ms", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("run_id", "sequence", name="uq_engineering_role_turn_sequence"),
    )
    for col in ["run_id","role","status","input_sha256"]:
        op.create_index(f"ix_engineering_role_turns_{col}", "engineering_role_turns", [col])
    op.create_index("ix_engineering_role_turns_run_created", "engineering_role_turns", ["run_id", "created_at"])

def downgrade():
    op.drop_table("engineering_role_turns")
    op.drop_table("engineering_runs")
