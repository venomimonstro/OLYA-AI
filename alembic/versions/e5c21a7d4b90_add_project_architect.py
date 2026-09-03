"""add project architect and sprint orchestrator

Revision ID: e5c21a7d4b90
Revises: d4a71f8c20b1
"""
from alembic import op
import sqlalchemy as sa

revision = "e5c21a7d4b90"
down_revision = "d4a71f8c20b1"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "development_plans",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("runtime_id", sa.String(36), sa.ForeignKey("project_runtimes.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_by", sa.String(36), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("product_brief", sa.Text(), nullable=False),
        sa.Column("requirements", sa.JSON(), nullable=False),
        sa.Column("architecture", sa.JSON(), nullable=False),
        sa.Column("constraints", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("current_sprint_ordinal", sa.Integer(), nullable=True),
        sa.Column("state_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("project_id", name="uq_development_plan_project"),
    )
    for col in ["project_id", "runtime_id", "created_by", "status"]:
        op.create_index(f"ix_development_plans_{col}", "development_plans", [col])
    op.create_index("ix_development_plans_status_updated", "development_plans", ["status", "updated_at"])

    op.create_table(
        "development_sprints",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("plan_id", sa.String(36), sa.ForeignKey("development_plans.id", ondelete="CASCADE"), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("goal", sa.Text(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("dependencies", sa.JSON(), nullable=False),
        sa.Column("acceptance_criteria", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("plan_id", "ordinal", name="uq_development_sprint_ordinal"),
    )
    op.create_index("ix_development_sprints_plan_id", "development_sprints", ["plan_id"])
    op.create_index("ix_development_sprints_status", "development_sprints", ["status"])
    op.create_index("ix_development_sprints_plan_status", "development_sprints", ["plan_id", "status"])

    op.create_table(
        "development_work_items",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("sprint_id", sa.String(36), sa.ForeignKey("development_sprints.id", ondelete="CASCADE"), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("goal", sa.Text(), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("dependencies", sa.JSON(), nullable=False),
        sa.Column("acceptance_criteria", sa.JSON(), nullable=False),
        sa.Column("task_id", sa.String(36), sa.ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("sprint_id", "ordinal", name="uq_development_work_item_ordinal"),
    )
    for col in ["sprint_id", "kind", "status", "task_id"]:
        op.create_index(f"ix_development_work_items_{col}", "development_work_items", [col])
    op.create_index("ix_development_work_items_sprint_status", "development_work_items", ["sprint_id", "status"])

    op.create_table(
        "architecture_decisions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("plan_id", sa.String(36), sa.ForeignKey("development_plans.id", ondelete="CASCADE"), nullable=False),
        sa.Column("key", sa.String(120), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("decision", sa.Text(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("created_by", sa.String(36), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("plan_id", "key", name="uq_architecture_decision_key"),
    )
    for col in ["plan_id", "status", "created_by"]:
        op.create_index(f"ix_architecture_decisions_{col}", "architecture_decisions", [col])
    op.create_index("ix_architecture_decisions_plan_status", "architecture_decisions", ["plan_id", "status"])

    op.create_table(
        "development_checkpoints",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("plan_id", sa.String(36), sa.ForeignKey("development_plans.id", ondelete="CASCADE"), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("plan_state_version", sa.Integer(), nullable=False),
        sa.Column("current_sprint_ordinal", sa.Integer(), nullable=True),
        sa.Column("runtime_snapshot_id", sa.String(36), sa.ForeignKey("project_runtime_snapshots.id", ondelete="SET NULL"), nullable=True),
        sa.Column("state_sha256", sa.String(64), nullable=False),
        sa.Column("state", sa.JSON(), nullable=False),
        sa.Column("reason", sa.String(200), nullable=False),
        sa.Column("created_by", sa.String(36), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("plan_id", "sequence", name="uq_development_checkpoint_sequence"),
    )
    for col in ["plan_id", "runtime_snapshot_id", "state_sha256", "created_by"]:
        op.create_index(f"ix_development_checkpoints_{col}", "development_checkpoints", [col])
    op.create_index("ix_development_checkpoints_plan_sequence", "development_checkpoints", ["plan_id", "sequence"])


def downgrade():
    op.drop_table("development_checkpoints")
    op.drop_table("architecture_decisions")
    op.drop_table("development_work_items")
    op.drop_table("development_sprints")
    op.drop_table("development_plans")
