"""add closed beta metrics

Revision ID: f29b3e71c902
Revises: f27c01a4b8e2
"""
from alembic import op
import sqlalchemy as sa

revision = "f29b3e71c902"
down_revision = "f27c01a4b8e2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "beta_participants",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("cohort", sa.String(length=64), nullable=False),
        sa.Column("state", sa.String(length=24), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("enrolled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "cohort", name="uq_beta_participant_user_cohort"),
    )
    op.create_index("ix_beta_participant_cohort_state", "beta_participants", ["cohort", "state"])
    op.create_index("ix_beta_participant_enrolled", "beta_participants", ["enrolled_at"])
    op.create_index("ix_beta_participants_user_id", "beta_participants", ["user_id"])
    op.create_index("ix_beta_participants_cohort", "beta_participants", ["cohort"])
    op.create_index("ix_beta_participants_state", "beta_participants", ["state"])
    op.create_table(
        "beta_snapshots",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("cohort", sa.String(length=64), nullable=False),
        sa.Column("window_days", sa.Integer(), nullable=False),
        sa.Column("enrolled_count", sa.Integer(), nullable=False),
        sa.Column("activated_count", sa.Integer(), nullable=False),
        sa.Column("d1_eligible_count", sa.Integer(), nullable=False),
        sa.Column("d1_retained_count", sa.Integer(), nullable=False),
        sa.Column("d7_eligible_count", sa.Integer(), nullable=False),
        sa.Column("d7_retained_count", sa.Integer(), nullable=False),
        sa.Column("request_count", sa.Integer(), nullable=False),
        sa.Column("success_count", sa.Integer(), nullable=False),
        sa.Column("task_count", sa.Integer(), nullable=False),
        sa.Column("completed_task_count", sa.Integer(), nullable=False),
        sa.Column("frustration_count", sa.Integer(), nullable=False),
        sa.Column("compute_minutes_total", sa.Float(), nullable=False),
        sa.Column("compute_minutes_per_active_user", sa.Float(), nullable=False),
        sa.Column("p95_duration_ms", sa.Integer(), nullable=False),
        sa.Column("p95_queue_ms", sa.Integer(), nullable=False),
        sa.Column("metrics", sa.JSON(), nullable=False),
        sa.Column("readiness", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_beta_snapshot_cohort_created", "beta_snapshots", ["cohort", "created_at"])
    op.create_index("ix_beta_snapshots_cohort", "beta_snapshots", ["cohort"])
    op.create_index("ix_beta_snapshots_created_at", "beta_snapshots", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_beta_snapshots_created_at", table_name="beta_snapshots")
    op.drop_index("ix_beta_snapshots_cohort", table_name="beta_snapshots")
    op.drop_index("ix_beta_snapshot_cohort_created", table_name="beta_snapshots")
    op.drop_table("beta_snapshots")
    op.drop_index("ix_beta_participants_state", table_name="beta_participants")
    op.drop_index("ix_beta_participants_cohort", table_name="beta_participants")
    op.drop_index("ix_beta_participants_user_id", table_name="beta_participants")
    op.drop_index("ix_beta_participant_enrolled", table_name="beta_participants")
    op.drop_index("ix_beta_participant_cohort_state", table_name="beta_participants")
    op.drop_table("beta_participants")
