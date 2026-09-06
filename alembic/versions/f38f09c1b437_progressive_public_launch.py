"""progressive public launch and measured plan catalog

Revision ID: f38f09c1b437
Revises: f37e6b04a325
"""
from alembic import op
import sqlalchemy as sa

revision = "f38f09c1b437"
down_revision = "f37e6b04a325"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "public_rollouts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(24), nullable=False, server_default="planned"),
        sa.Column("exposure_percent", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("assignment_salt", sa.String(64), nullable=False),
        sa.Column("baseline_metrics", sa.JSON(), nullable=False),
        sa.Column("latest_metrics", sa.JSON(), nullable=False),
        sa.Column("guardrails", sa.JSON(), nullable=False),
        sa.Column("decision", sa.JSON(), nullable=False),
        sa.Column("previous_rollout_id", sa.String(36), sa.ForeignKey("public_rollouts.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_by", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("approved_by", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("paused_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rolled_back_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("version", name="uq_public_rollout_version"),
    )
    op.create_index("ix_public_rollouts_version", "public_rollouts", ["version"])
    op.create_index("ix_public_rollouts_state", "public_rollouts", ["state"])
    op.create_index("ix_public_rollouts_previous_rollout_id", "public_rollouts", ["previous_rollout_id"])
    op.create_index("ix_public_rollouts_created_at", "public_rollouts", ["created_at"])
    op.create_index("ix_public_rollout_state_created", "public_rollouts", ["state", "created_at"])

    op.create_table(
        "measured_plan_catalogs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="draft"),
        sa.Column("source", sa.String(64), nullable=False, server_default="closed-beta-measured"),
        sa.Column("catalog", sa.JSON(), nullable=False),
        sa.Column("economics", sa.JSON(), nullable=False),
        sa.Column("guardrails", sa.JSON(), nullable=False),
        sa.Column("previous_catalog_id", sa.String(36), sa.ForeignKey("measured_plan_catalogs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_by", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("approved_by", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("version", name="uq_measured_plan_catalog_version"),
    )
    op.create_index("ix_measured_plan_catalogs_version", "measured_plan_catalogs", ["version"])
    op.create_index("ix_measured_plan_catalogs_status", "measured_plan_catalogs", ["status"])
    op.create_index("ix_measured_plan_catalogs_previous_catalog_id", "measured_plan_catalogs", ["previous_catalog_id"])
    op.create_index("ix_measured_plan_catalogs_created_at", "measured_plan_catalogs", ["created_at"])
    op.create_index("ix_measured_plan_catalog_status_created", "measured_plan_catalogs", ["status", "created_at"])

    op.create_table(
        "circuit_breaker_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("scope", sa.String(64), nullable=False, server_default="public-launch"),
        sa.Column("kind", sa.String(48), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="open"),
        sa.Column("automatic", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("reason", sa.String(500), nullable=False, server_default=""),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("opened_by", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("resolved_by", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_circuit_breaker_events_scope", "circuit_breaker_events", ["scope"])
    op.create_index("ix_circuit_breaker_events_kind", "circuit_breaker_events", ["kind"])
    op.create_index("ix_circuit_breaker_events_status", "circuit_breaker_events", ["status"])
    op.create_index("ix_circuit_breaker_events_opened_at", "circuit_breaker_events", ["opened_at"])
    op.create_index("ix_circuit_breaker_status_scope", "circuit_breaker_events", ["status", "scope"])
    op.create_index("ix_circuit_breaker_opened", "circuit_breaker_events", ["opened_at"])


def downgrade() -> None:
    op.drop_table("circuit_breaker_events")
    op.drop_table("measured_plan_catalogs")
    op.drop_table("public_rollouts")
