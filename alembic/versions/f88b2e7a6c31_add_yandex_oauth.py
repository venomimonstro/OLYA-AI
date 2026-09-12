"""add Yandex OAuth login

Revision ID: f88b2e7a6c31
Revises: f87a1d9c4e20
"""
from alembic import op
import sqlalchemy as sa

revision = "f88b2e7a6c31"
down_revision = "f87a1d9c4e20"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("owner_integration_settings", sa.Column("yandex_oauth_enabled", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("owner_integration_settings", sa.Column("yandex_oauth_client_id", sa.String(160), nullable=False, server_default=""))
    op.add_column("owner_integration_settings", sa.Column("yandex_oauth_client_secret_ciphertext", sa.Text(), nullable=False, server_default=""))

    op.create_table(
        "external_auth_identities",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("provider", sa.String(24), nullable=False),
        sa.Column("subject", sa.String(160), nullable=False),
        sa.Column("email_at_link", sa.String(320), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("provider", "subject", name="uq_external_auth_provider_subject"),
        sa.UniqueConstraint("provider", "user_id", name="uq_external_auth_provider_user"),
    )
    op.create_index("ix_external_auth_identities_user_id", "external_auth_identities", ["user_id"])
    op.create_index("ix_external_auth_identities_provider", "external_auth_identities", ["provider"])
    op.create_index("ix_external_auth_user_provider", "external_auth_identities", ["user_id", "provider"])

    op.create_table(
        "oauth_login_states",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("provider", sa.String(24), nullable=False),
        sa.Column("state_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("pkce_verifier_ciphertext", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_oauth_login_states_provider", "oauth_login_states", ["provider"])
    op.create_index("ix_oauth_login_states_state_hash", "oauth_login_states", ["state_hash"], unique=True)
    op.create_index("ix_oauth_login_states_expires_at", "oauth_login_states", ["expires_at"])
    op.create_index("ix_oauth_login_states_expiry", "oauth_login_states", ["provider", "expires_at", "used_at"])


def downgrade() -> None:
    op.drop_table("oauth_login_states")
    op.drop_table("external_auth_identities")
    op.drop_column("owner_integration_settings", "yandex_oauth_client_secret_ciphertext")
    op.drop_column("owner_integration_settings", "yandex_oauth_client_id")
    op.drop_column("owner_integration_settings", "yandex_oauth_enabled")
