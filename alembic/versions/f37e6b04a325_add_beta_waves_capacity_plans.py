"""add beta waves and versioned capacity plans

Revision ID: f37e6b04a325
Revises: f31d5a93e214
"""
from alembic import op
import sqlalchemy as sa

revision = "f37e6b04a325"
down_revision = "f31d5a93e214"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "beta_waves",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("cohort", sa.String(64), nullable=False),
        sa.Column("wave_number", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(24), nullable=False, server_default="planned"),
        sa.Column("target_participants", sa.Integer(), nullable=False, server_default="10"),
        sa.Column("admitted_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("window_days", sa.Integer(), nullable=False, server_default="30"),
        sa.Column("compute_budget_seconds", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("admission_paused", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("pause_reason", sa.String(500), nullable=False, server_default=""),
        sa.Column("baseline_metrics", sa.JSON(), nullable=False),
        sa.Column("latest_metrics", sa.JSON(), nullable=False),
        sa.Column("decision", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("observing_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("cohort", "wave_number", name="uq_beta_wave_cohort_number"),
    )
    op.create_index("ix_beta_waves_cohort", "beta_waves", ["cohort"])
    op.create_index("ix_beta_waves_state", "beta_waves", ["state"])
    op.create_index("ix_beta_waves_admission_paused", "beta_waves", ["admission_paused"])
    op.create_index("ix_beta_waves_created_by", "beta_waves", ["created_by"])
    op.create_index("ix_beta_waves_created_at", "beta_waves", ["created_at"])
    op.create_index("ix_beta_wave_cohort_state", "beta_waves", ["cohort", "state"])
    op.create_index("ix_beta_wave_created", "beta_waves", ["created_at"])

    op.create_table(
        "capacity_plans",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="draft"),
        sa.Column("source", sa.String(64), nullable=False, server_default="sprint37-calibration"),
        sa.Column("plan", sa.JSON(), nullable=False),
        sa.Column("signals", sa.JSON(), nullable=False),
        sa.Column("guardrails", sa.JSON(), nullable=False),
        sa.Column("comparison", sa.JSON(), nullable=False),
        sa.Column("previous_plan_id", sa.String(36), sa.ForeignKey("capacity_plans.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_by", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("approved_by", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("rollback_of_id", sa.String(36), sa.ForeignKey("capacity_plans.id", ondelete="SET NULL"), nullable=True),
        sa.Column("requires_restart", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("runtime_applied", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rolled_back_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("version", name="uq_capacity_plan_version"),
    )
    op.create_index("ix_capacity_plans_version", "capacity_plans", ["version"])
    op.create_index("ix_capacity_plans_status", "capacity_plans", ["status"])
    op.create_index("ix_capacity_plans_previous_plan_id", "capacity_plans", ["previous_plan_id"])
    op.create_index("ix_capacity_plans_created_by", "capacity_plans", ["created_by"])
    op.create_index("ix_capacity_plans_approved_by", "capacity_plans", ["approved_by"])
    op.create_index("ix_capacity_plans_rollback_of_id", "capacity_plans", ["rollback_of_id"])
    op.create_index("ix_capacity_plans_created_at", "capacity_plans", ["created_at"])
    op.create_index("ix_capacity_plan_status_created", "capacity_plans", ["status", "created_at"])


def downgrade() -> None:
    op.drop_table("capacity_plans")
    op.drop_table("beta_waves")
