"""add reliability observatory

Revision ID: f31d5a93e214
Revises: f30c4a82d103
"""
from alembic import op
import sqlalchemy as sa

revision = "f31d5a93e214"
down_revision = "f30c4a82d103"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "system_checkpoints",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("key", sa.String(160), nullable=False),
        sa.Column("subsystem", sa.String(80), nullable=False),
        sa.Column("dependency", sa.String(160), nullable=False, server_default=""),
        sa.Column("status", sa.String(16), nullable=False, server_default="unknown"),
        sa.Column("severity", sa.String(16), nullable=False, server_default="info"),
        sa.Column("critical", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("latency_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("message", sa.String(500), nullable=False, server_default=""),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_ok_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("key", name="uq_system_checkpoint_key"),
    )
    op.create_index("ix_system_checkpoints_key", "system_checkpoints", ["key"])
    op.create_index("ix_system_checkpoints_subsystem", "system_checkpoints", ["subsystem"])
    op.create_index("ix_system_checkpoints_status", "system_checkpoints", ["status"])
    op.create_index("ix_system_checkpoints_severity", "system_checkpoints", ["severity"])
    op.create_index("ix_system_checkpoints_critical", "system_checkpoints", ["critical"])
    op.create_index("ix_system_checkpoints_last_checked_at", "system_checkpoints", ["last_checked_at"])
    op.create_index("ix_system_checkpoint_status_checked", "system_checkpoints", ["status", "last_checked_at"])
    op.create_index("ix_system_checkpoint_subsystem_status", "system_checkpoints", ["subsystem", "status"])

    op.create_table(
        "system_health_snapshots",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("overall_status", sa.String(16), nullable=False),
        sa.Column("score", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("stable_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("degraded_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("critical_failed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("checks", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_system_health_snapshots_overall_status", "system_health_snapshots", ["overall_status"])
    op.create_index("ix_system_health_snapshots_created_at", "system_health_snapshots", ["created_at"])
    op.create_index("ix_system_health_snapshot_created", "system_health_snapshots", ["created_at"])


def downgrade() -> None:
    op.drop_table("system_health_snapshots")
    op.drop_table("system_checkpoints")
