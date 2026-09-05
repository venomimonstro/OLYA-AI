"""add complaint regression release gate

Revision ID: f30c4a82d103
Revises: f29b3e71c902
"""
from alembic import op
import sqlalchemy as sa

revision = "f30c4a82d103"
down_revision = "f29b3e71c902"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "complaint_cases",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("reporter_user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id", ondelete="SET NULL"), nullable=True),
        sa.Column("conversation_id", sa.String(36), sa.ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True),
        sa.Column("request_id", sa.String(64), nullable=True),
        sa.Column("component", sa.String(80), nullable=False),
        sa.Column("category", sa.String(64), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False, server_default="medium"),
        sa.Column("title", sa.String(240), nullable=False),
        sa.Column("expected_behavior", sa.Text(), nullable=False, server_default=""),
        sa.Column("actual_behavior", sa.Text(), nullable=False),
        sa.Column("reproduction", sa.JSON(), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="new"),
        sa.Column("confirmed_by", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    for name in ("reporter_user_id", "project_id", "conversation_id", "request_id", "component", "category", "severity", "fingerprint", "status", "created_at"):
        op.create_index(f"ix_complaint_cases_{name}", "complaint_cases", [name])
    op.create_index("ix_complaint_cases_fingerprint_status", "complaint_cases", ["fingerprint", "status"])
    op.create_index("ix_complaint_cases_category_created", "complaint_cases", ["category", "created_at"])
    op.create_index("ix_complaint_cases_reporter_created", "complaint_cases", ["reporter_user_id", "created_at"])

    op.create_table(
        "regression_cases",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("source_complaint_id", sa.String(36), sa.ForeignKey("complaint_cases.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("component", sa.String(80), nullable=False),
        sa.Column("category", sa.String(64), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False, server_default="medium"),
        sa.Column("title", sa.String(240), nullable=False),
        sa.Column("spec", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="active"),
        sa.Column("confirmed_occurrences", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("release_blocking", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("last_confirmed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("latest_result", sa.String(24), nullable=False, server_default="not_run"),
        sa.Column("latest_release", sa.String(80), nullable=False, server_default=""),
        sa.Column("latest_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("fingerprint", name="uq_regression_case_fingerprint"),
    )
    for name in ("source_complaint_id", "fingerprint", "component", "category", "severity", "status", "release_blocking", "latest_result"):
        op.create_index(f"ix_regression_cases_{name}", "regression_cases", [name])
    op.create_index("ix_regression_cases_blocking_status", "regression_cases", ["release_blocking", "status"])

    op.create_table(
        "regression_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("regression_case_id", sa.String(36), sa.ForeignKey("regression_cases.id", ondelete="CASCADE"), nullable=False),
        sa.Column("release_version", sa.String(80), nullable=False),
        sa.Column("result", sa.String(16), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("executed_by", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    for name in ("regression_case_id", "release_version", "result", "created_at"):
        op.create_index(f"ix_regression_runs_{name}", "regression_runs", [name])
    op.create_index("ix_regression_runs_case_created", "regression_runs", ["regression_case_id", "created_at"])
    op.create_index("ix_regression_runs_release_result", "regression_runs", ["release_version", "result"])

    op.create_table(
        "release_gate_decisions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("release_version", sa.String(80), nullable=False),
        sa.Column("channel", sa.String(24), nullable=False, server_default="stable"),
        sa.Column("decision", sa.String(16), nullable=False),
        sa.Column("blocker_ids", sa.JSON(), nullable=False),
        sa.Column("reasons", sa.JSON(), nullable=False),
        sa.Column("evaluated_by", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    for name in ("release_version", "channel", "decision", "created_at"):
        op.create_index(f"ix_release_gate_decisions_{name}", "release_gate_decisions", [name])
    op.create_index("ix_release_gate_release_created", "release_gate_decisions", ["release_version", "created_at"])


def downgrade() -> None:
    op.drop_table("release_gate_decisions")
    op.drop_table("regression_runs")
    op.drop_table("regression_cases")
    op.drop_table("complaint_cases")
