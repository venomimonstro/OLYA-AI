"""add commerce business and api control plane

Revision ID: f27c01a4b8e2
Revises: d26a91f3b7c4
"""
from alembic import op
import sqlalchemy as sa

revision = "f27c01a4b8e2"
down_revision = "d26a91f3b7c4"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "organizations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_id", sa.String(36), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("slug", sa.String(120), nullable=False),
        sa.Column("plan", sa.String(24), nullable=False, server_default="business"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("slug", name="uq_organizations_slug"),
    )
    op.create_index("ix_organizations_owner_id", "organizations", ["owner_id"])
    op.create_index("ix_organizations_slug", "organizations", ["slug"])
    op.create_index("ix_organizations_plan", "organizations", ["plan"])

    op.create_table(
        "organization_members",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("organization_id", sa.String(36), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.String(24), nullable=False, server_default="member"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("organization_id", "user_id", name="uq_organization_member"),
    )
    op.create_index("ix_organization_members_organization_id", "organization_members", ["organization_id"])
    op.create_index("ix_organization_members_user_id", "organization_members", ["user_id"])
    op.create_index("ix_organization_members_role", "organization_members", ["role"])
    op.create_index("ix_organization_member_user_org", "organization_members", ["user_id", "organization_id"])

    op.create_table(
        "organization_budgets",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("organization_id", sa.String(36), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("month", sa.String(7), nullable=False),
        sa.Column("limit_microunits", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("alert_percent", sa.Integer(), nullable=False, server_default="80"),
        sa.Column("hard_limit", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_by", sa.String(36), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("organization_id", "month", name="uq_organization_budget_month"),
    )
    for col in ["organization_id", "month", "created_by"]:
        op.create_index(f"ix_organization_budgets_{col}", "organization_budgets", [col])

    op.create_table(
        "payment_records",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("organization_id", sa.String(36), sa.ForeignKey("organizations.id", ondelete="SET NULL"), nullable=True),
        sa.Column("provider", sa.String(48), nullable=False),
        sa.Column("provider_event_id", sa.String(160), nullable=False),
        sa.Column("idempotency_key", sa.String(160), nullable=False),
        sa.Column("kind", sa.String(24), nullable=False, server_default="payment"),
        sa.Column("amount_minor", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False, server_default="RUB"),
        sa.Column("status", sa.String(24), nullable=False, server_default="received"),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reconciled_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("provider", "idempotency_key", name="uq_payment_provider_idempotency"),
        sa.UniqueConstraint("provider", "provider_event_id", name="uq_payment_provider_event"),
    )
    for col in ["user_id", "organization_id", "provider", "status"]:
        op.create_index(f"ix_payment_records_{col}", "payment_records", [col])
    op.create_index("ix_payment_status_created", "payment_records", ["status", "created_at"])

    op.create_table(
        "api_keys",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("organization_id", sa.String(36), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("prefix", sa.String(20), nullable=False),
        sa.Column("secret_hash", sa.String(64), nullable=False),
        sa.Column("scopes", sa.JSON(), nullable=False),
        sa.Column("rate_limit_per_minute", sa.Integer(), nullable=False, server_default="60"),
        sa.Column("status", sa.String(24), nullable=False, server_default="active"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("secret_hash", name="uq_api_key_secret_hash"),
    )
    for col in ["owner_id", "organization_id", "prefix", "status", "expires_at"]:
        op.create_index(f"ix_api_keys_{col}", "api_keys", [col])
    op.create_index("ix_api_keys_owner_status", "api_keys", ["owner_id", "status"])

    op.create_table(
        "api_rate_limit_windows",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("api_key_id", sa.String(36), sa.ForeignKey("api_keys.id", ondelete="CASCADE"), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("request_count", sa.Integer(), nullable=False, server_default="0"),
        sa.UniqueConstraint("api_key_id", "window_start", name="uq_api_rate_window"),
    )
    op.create_index("ix_api_rate_limit_windows_api_key_id", "api_rate_limit_windows", ["api_key_id"])
    op.create_index("ix_api_rate_limit_windows_window_start", "api_rate_limit_windows", ["window_start"])

    op.create_table(
        "persistent_api_contexts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("organization_id", sa.String(36), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=True),
        sa.Column("conversation_id", sa.String(36), sa.ForeignKey("conversations.id", ondelete="CASCADE"), nullable=True),
        sa.Column("label", sa.String(160), nullable=False, server_default=""),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    for col in ["owner_id", "organization_id", "project_id", "conversation_id"]:
        op.create_index(f"ix_persistent_api_contexts_{col}", "persistent_api_contexts", [col])
    op.create_index("ix_api_context_owner_updated", "persistent_api_contexts", ["owner_id", "updated_at"])

    op.create_table(
        "api_request_telemetry",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("api_key_id", sa.String(36), sa.ForeignKey("api_keys.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("organization_id", sa.String(36), sa.ForeignKey("organizations.id", ondelete="SET NULL"), nullable=True),
        sa.Column("context_id", sa.String(36), sa.ForeignKey("persistent_api_contexts.id", ondelete="SET NULL"), nullable=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id", ondelete="SET NULL"), nullable=True),
        sa.Column("endpoint", sa.String(160), nullable=False),
        sa.Column("request_id", sa.String(64), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("quality_status", sa.String(24), nullable=False, server_default="unknown"),
        sa.Column("cost_microunits", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("resource_usage", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("request_id", name="uq_api_telemetry_request_id"),
    )
    for col in ["api_key_id", "user_id", "organization_id", "context_id", "project_id", "endpoint", "quality_status"]:
        op.create_index(f"ix_api_request_telemetry_{col}", "api_request_telemetry", [col])
    op.create_index("ix_api_telemetry_key_created", "api_request_telemetry", ["api_key_id", "created_at"])
    op.create_index("ix_api_telemetry_owner_created", "api_request_telemetry", ["user_id", "created_at"])

    op.create_table(
        "resource_expense_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("organization_id", sa.String(36), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id", ondelete="SET NULL"), nullable=True),
        sa.Column("api_key_id", sa.String(36), sa.ForeignKey("api_keys.id", ondelete="SET NULL"), nullable=True),
        sa.Column("resource_kind", sa.String(32), nullable=False),
        sa.Column("quantity_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cost_microunits", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("source_kind", sa.String(48), nullable=False),
        sa.Column("source_id", sa.String(120), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("source_kind", "source_id", "resource_kind", name="uq_resource_expense_source"),
    )
    for col in ["user_id", "organization_id", "project_id", "api_key_id", "resource_kind", "source_kind", "source_id"]:
        op.create_index(f"ix_resource_expense_events_{col}", "resource_expense_events", [col])
    op.create_index("ix_resource_expense_org_created", "resource_expense_events", ["organization_id", "created_at"])
    op.create_index("ix_resource_expense_user_created", "resource_expense_events", ["user_id", "created_at"])


def downgrade():
    op.drop_table("resource_expense_events")
    op.drop_table("api_request_telemetry")
    op.drop_table("persistent_api_contexts")
    op.drop_table("api_rate_limit_windows")
    op.drop_table("api_keys")
    op.drop_table("payment_records")
    op.drop_table("organization_budgets")
    op.drop_table("organization_members")
    op.drop_table("organizations")
