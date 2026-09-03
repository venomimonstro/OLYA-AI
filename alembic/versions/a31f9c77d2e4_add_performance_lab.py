"""add performance lab

Revision ID: a31f9c77d2e4
Revises: 7d8c11b4a3f2
"""
from alembic import op
import sqlalchemy as sa

revision = "a31f9c77d2e4"
down_revision = "7d8c11b4a3f2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "performance_snapshots",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("window_minutes", sa.Integer(), nullable=False),
        sa.Column("request_count", sa.Integer(), nullable=False),
        sa.Column("success_count", sa.Integer(), nullable=False),
        sa.Column("p50_duration_ms", sa.Integer(), nullable=False),
        sa.Column("p95_duration_ms", sa.Integer(), nullable=False),
        sa.Column("p99_duration_ms", sa.Integer(), nullable=False),
        sa.Column("p50_queue_ms", sa.Integer(), nullable=False),
        sa.Column("p95_queue_ms", sa.Integer(), nullable=False),
        sa.Column("p95_inference_ms", sa.Integer(), nullable=False),
        sa.Column("cpu_seconds_per_success", sa.Float(), nullable=False),
        sa.Column("context_efficiency_ratio", sa.Float(), nullable=False),
        sa.Column("frustration_count", sa.Integer(), nullable=False),
        sa.Column("quality_failure_count", sa.Integer(), nullable=False),
        sa.Column("metrics", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_performance_snapshots_created", "performance_snapshots", ["created_at"])
    op.create_table(
        "optimization_experiments",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("baseline", sa.JSON(), nullable=False),
        sa.Column("candidate", sa.JSON(), nullable=False),
        sa.Column("decision", sa.String(length=24), nullable=False),
        sa.Column("reasons", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.String(length=36), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_optimization_experiments_created", "optimization_experiments", ["created_at"])
    op.create_index("ix_optimization_experiments_decision", "optimization_experiments", ["decision"])
    op.create_index("ix_optimization_experiments_created_by", "optimization_experiments", ["created_by"])


def downgrade() -> None:
    op.drop_index("ix_optimization_experiments_created_by", table_name="optimization_experiments")
    op.drop_index("ix_optimization_experiments_decision", table_name="optimization_experiments")
    op.drop_index("ix_optimization_experiments_created", table_name="optimization_experiments")
    op.drop_table("optimization_experiments")
    op.drop_index("ix_performance_snapshots_created", table_name="performance_snapshots")
    op.drop_table("performance_snapshots")
