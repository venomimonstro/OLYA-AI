"""add compute breakdown events

Revision ID: f53b21e7c4a0
Revises: f51c0a11d9e2
Create Date: 2026-09-08
"""

from alembic import op
import sqlalchemy as sa

revision = "f53b21e7c4a0"
down_revision = "f51c0a11d9e2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "compute_breakdown_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("request_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=True),
        sa.Column("conversation_id", sa.String(length=36), nullable=True),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.Column("primary_ms", sa.Integer(), nullable=False),
        sa.Column("critic_ms", sa.Integer(), nullable=False),
        sa.Column("repair_ms", sa.Integer(), nullable=False),
        sa.Column("total_inference_ms", sa.Integer(), nullable=False),
        sa.Column("wasted_ms", sa.Integer(), nullable=False),
        sa.Column("success", sa.Boolean(), nullable=False),
        sa.Column("verification_extra_inferences", sa.Integer(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("request_id", name="uq_compute_breakdown_request"),
    )
    for name, columns in (
        ("ix_compute_breakdown_events_request_id", ["request_id"]),
        ("ix_compute_breakdown_events_user_id", ["user_id"]),
        ("ix_compute_breakdown_events_project_id", ["project_id"]),
        ("ix_compute_breakdown_events_conversation_id", ["conversation_id"]),
        ("ix_compute_breakdown_events_mode", ["mode"]),
        ("ix_compute_breakdown_events_success", ["success"]),
        ("ix_compute_breakdown_events_created_at", ["created_at"]),
        ("ix_compute_breakdown_user_created", ["user_id", "created_at"]),
        ("ix_compute_breakdown_mode_created", ["mode", "created_at"]),
        ("ix_compute_breakdown_success_created", ["success", "created_at"]),
    ):
        op.create_index(name, "compute_breakdown_events", columns, unique=False)


def downgrade() -> None:
    for name in (
        "ix_compute_breakdown_success_created",
        "ix_compute_breakdown_mode_created",
        "ix_compute_breakdown_user_created",
        "ix_compute_breakdown_events_created_at",
        "ix_compute_breakdown_events_success",
        "ix_compute_breakdown_events_mode",
        "ix_compute_breakdown_events_conversation_id",
        "ix_compute_breakdown_events_project_id",
        "ix_compute_breakdown_events_user_id",
        "ix_compute_breakdown_events_request_id",
    ):
        op.drop_index(name, table_name="compute_breakdown_events")
    op.drop_table("compute_breakdown_events")
