"""add autonomous development ledger

Revision ID: f51c0a11d9e2
Revises: f45a10c2d8e1
Create Date: 2026-09-08
"""

from alembic import op
import sqlalchemy as sa

revision = "f51c0a11d9e2"
down_revision = "f45a10c2d8e1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "autonomous_development_ledgers",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("development_session_id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("plan_id", sa.String(length=36), nullable=False),
        sa.Column("immutable_goal", sa.Text(), nullable=False),
        sa.Column("immutable_constraints", sa.JSON(), nullable=False),
        sa.Column("contract_sha256", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("current_state", sa.JSON(), nullable=False),
        sa.Column("decisions", sa.JSON(), nullable=False),
        sa.Column("completed_work", sa.JSON(), nullable=False),
        sa.Column("pending_work", sa.JSON(), nullable=False),
        sa.Column("failed_work", sa.JSON(), nullable=False),
        sa.Column("checkpoint_ref", sa.String(length=36), nullable=False),
        sa.Column("subagent_budget", sa.Integer(), nullable=False),
        sa.Column("max_parallel_subagents", sa.Integer(), nullable=False),
        sa.Column("subagent_calls_used", sa.Integer(), nullable=False),
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("development_session_id", name="uq_autonomous_development_ledger_session"),
    )
    op.create_index("ix_autonomous_development_ledger_project_status", "autonomous_development_ledgers", ["project_id", "status"], unique=False)
    op.create_index("ix_autonomous_development_ledgers_development_session_id", "autonomous_development_ledgers", ["development_session_id"], unique=False)
    op.create_index("ix_autonomous_development_ledgers_project_id", "autonomous_development_ledgers", ["project_id"], unique=False)
    op.create_index("ix_autonomous_development_ledgers_plan_id", "autonomous_development_ledgers", ["plan_id"], unique=False)
    op.create_index("ix_autonomous_development_ledgers_contract_sha256", "autonomous_development_ledgers", ["contract_sha256"], unique=False)
    op.create_index("ix_autonomous_development_ledgers_status", "autonomous_development_ledgers", ["status"], unique=False)
    op.create_index("ix_autonomous_development_ledgers_last_heartbeat_at", "autonomous_development_ledgers", ["last_heartbeat_at"], unique=False)

    op.create_table(
        "autonomous_development_checkpoints",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("ledger_id", sa.String(length=36), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("state_sha256", sa.String(length=64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("ledger_id", "revision", name="uq_autonomous_development_checkpoint_revision"),
    )
    op.create_index("ix_autonomous_development_checkpoint_ledger_created", "autonomous_development_checkpoints", ["ledger_id", "created_at"], unique=False)
    op.create_index("ix_autonomous_development_checkpoints_ledger_id", "autonomous_development_checkpoints", ["ledger_id"], unique=False)
    op.create_index("ix_autonomous_development_checkpoints_state_sha256", "autonomous_development_checkpoints", ["state_sha256"], unique=False)
    op.create_index("ix_autonomous_development_checkpoints_created_at", "autonomous_development_checkpoints", ["created_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_autonomous_development_checkpoints_created_at", table_name="autonomous_development_checkpoints")
    op.drop_index("ix_autonomous_development_checkpoints_state_sha256", table_name="autonomous_development_checkpoints")
    op.drop_index("ix_autonomous_development_checkpoints_ledger_id", table_name="autonomous_development_checkpoints")
    op.drop_index("ix_autonomous_development_checkpoint_ledger_created", table_name="autonomous_development_checkpoints")
    op.drop_table("autonomous_development_checkpoints")
    op.drop_index("ix_autonomous_development_ledgers_last_heartbeat_at", table_name="autonomous_development_ledgers")
    op.drop_index("ix_autonomous_development_ledgers_status", table_name="autonomous_development_ledgers")
    op.drop_index("ix_autonomous_development_ledgers_contract_sha256", table_name="autonomous_development_ledgers")
    op.drop_index("ix_autonomous_development_ledgers_plan_id", table_name="autonomous_development_ledgers")
    op.drop_index("ix_autonomous_development_ledgers_project_id", table_name="autonomous_development_ledgers")
    op.drop_index("ix_autonomous_development_ledgers_development_session_id", table_name="autonomous_development_ledgers")
    op.drop_index("ix_autonomous_development_ledger_project_status", table_name="autonomous_development_ledgers")
    op.drop_table("autonomous_development_ledgers")
