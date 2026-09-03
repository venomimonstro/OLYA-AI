"""add safety review center

Revision ID: 6b7e91af21c3
Revises: 4ac91e0f77d2
Create Date: 2026-09-01
"""
from alembic import op
import sqlalchemy as sa

revision = "6b7e91af21c3"
down_revision = "4ac91e0f77d2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "risk_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id", ondelete="SET NULL"), nullable=True),
        sa.Column("conversation_id", sa.String(36), sa.ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True),
        sa.Column("message_id", sa.String(36), sa.ForeignKey("messages.id", ondelete="SET NULL"), nullable=True),
        sa.Column("category", sa.String(64), nullable=False),
        sa.Column("severity", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("rule_id", sa.String(120), nullable=False, server_default=""),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("detected_by", sa.String(32), nullable=False, server_default="system"),
        sa.Column("state", sa.String(24), nullable=False, server_default="new"),
        sa.Column("reviewed_by", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    for name, cols in [
        ("ix_risk_events_user_id", ["user_id"]), ("ix_risk_events_project_id", ["project_id"]),
        ("ix_risk_events_conversation_id", ["conversation_id"]), ("ix_risk_events_message_id", ["message_id"]),
        ("ix_risk_events_category", ["category"]), ("ix_risk_events_severity", ["severity"]), ("ix_risk_events_state", ["state"]),
        ("ix_risk_events_state_severity", ["state", "severity"]), ("ix_risk_events_user_created", ["user_id", "created_at"]),
    ]: op.create_index(name, "risk_events", cols)

    op.create_table(
        "safety_cases",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="open"),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("title", sa.String(240), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("risk_event_ids", sa.JSON(), nullable=False),
        sa.Column("assigned_admin_id", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("decision", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_by", sa.String(36), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
    )
    for name, cols in [
        ("ix_safety_cases_user_id", ["user_id"]), ("ix_safety_cases_status", ["status"]), ("ix_safety_cases_priority", ["priority"]),
        ("ix_safety_cases_assigned_admin_id", ["assigned_admin_id"]), ("ix_safety_cases_created_by", ["created_by"]),
        ("ix_safety_cases_status_priority", ["status", "priority"]), ("ix_safety_cases_user_created", ["user_id", "created_at"]),
    ]: op.create_index(name, "safety_cases", cols)

    op.create_table(
        "user_restrictions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("case_id", sa.String(36), sa.ForeignKey("safety_cases.id", ondelete="SET NULL"), nullable=True),
        sa.Column("capability", sa.String(32), nullable=False, server_default="all"),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.String(36), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_by", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    for name, cols in [
        ("ix_user_restrictions_user_id", ["user_id"]), ("ix_user_restrictions_case_id", ["case_id"]),
        ("ix_user_restrictions_capability", ["capability"]), ("ix_user_restrictions_active", ["active"]),
        ("ix_user_restrictions_expires_at", ["expires_at"]), ("ix_user_restrictions_created_by", ["created_by"]),
        ("ix_user_restrictions_user_active", ["user_id", "active"]),
    ]: op.create_index(name, "user_restrictions", cols)

    op.create_table(
        "legal_reviews",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("case_id", sa.String(36), sa.ForeignKey("safety_cases.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="draft"),
        sa.Column("legal_basis", sa.Text(), nullable=False, server_default=""),
        sa.Column("requested_scope", sa.JSON(), nullable=False),
        sa.Column("justification", sa.Text(), nullable=False, server_default=""),
        sa.Column("requested_by", sa.String(36), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("approved_by", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("export_manifest", sa.JSON(), nullable=False),
        sa.Column("export_sha256", sa.String(64), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    for name, cols in [
        ("ix_legal_reviews_case_id", ["case_id"]), ("ix_legal_reviews_status", ["status"]),
        ("ix_legal_reviews_requested_by", ["requested_by"]), ("ix_legal_reviews_status_created", ["status", "created_at"]),
    ]: op.create_index(name, "legal_reviews", cols)


def downgrade() -> None:
    for name in ["ix_legal_reviews_status_created","ix_legal_reviews_requested_by","ix_legal_reviews_status","ix_legal_reviews_case_id"]:
        op.drop_index(name, table_name="legal_reviews")
    op.drop_table("legal_reviews")
    for name in ["ix_user_restrictions_user_active","ix_user_restrictions_created_by","ix_user_restrictions_expires_at","ix_user_restrictions_active","ix_user_restrictions_capability","ix_user_restrictions_case_id","ix_user_restrictions_user_id"]:
        op.drop_index(name, table_name="user_restrictions")
    op.drop_table("user_restrictions")
    for name in ["ix_safety_cases_user_created","ix_safety_cases_status_priority","ix_safety_cases_created_by","ix_safety_cases_assigned_admin_id","ix_safety_cases_priority","ix_safety_cases_status","ix_safety_cases_user_id"]:
        op.drop_index(name, table_name="safety_cases")
    op.drop_table("safety_cases")
    for name in ["ix_risk_events_user_created","ix_risk_events_state_severity","ix_risk_events_state","ix_risk_events_severity","ix_risk_events_category","ix_risk_events_message_id","ix_risk_events_conversation_id","ix_risk_events_project_id","ix_risk_events_user_id"]:
        op.drop_index(name, table_name="risk_events")
    op.drop_table("risk_events")
