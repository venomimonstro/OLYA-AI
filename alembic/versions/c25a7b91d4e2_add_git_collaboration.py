"""add git collaboration tables

Revision ID: c25a7b91d4e2
Revises: b24f0c91a7d2
"""
from alembic import op
import sqlalchemy as sa

revision = "c25a7b91d4e2"
down_revision = "b24f0c91a7d2"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "git_repository_bindings",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("runtime_id", sa.String(36), sa.ForeignKey("project_runtimes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("workspace_id", sa.String(36), sa.ForeignKey("code_workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_by", sa.String(36), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("provider", sa.String(24), nullable=False, server_default="local"),
        sa.Column("repository_url", sa.Text(), nullable=False, server_default=""),
        sa.Column("repository_owner", sa.String(160), nullable=False, server_default=""),
        sa.Column("repository_name", sa.String(160), nullable=False, server_default=""),
        sa.Column("default_branch", sa.String(120), nullable=False, server_default="main"),
        sa.Column("working_branch", sa.String(160), nullable=False, server_default=""),
        sa.Column("mode", sa.String(24), nullable=False, server_default="local_only"),
        sa.Column("push_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("credential_secret_name", sa.String(120), nullable=False, server_default=""),
        sa.Column("status", sa.String(24), nullable=False, server_default="configured"),
        sa.Column("last_local_head", sa.String(64), nullable=False, server_default=""),
        sa.Column("last_remote_head", sa.String(64), nullable=False, server_default=""),
        sa.Column("state_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("runtime_id", name="uq_git_binding_runtime"),
    )
    op.create_index("ix_git_repository_bindings_project_id", "git_repository_bindings", ["project_id"])
    op.create_index("ix_git_repository_bindings_runtime_id", "git_repository_bindings", ["runtime_id"])
    op.create_index("ix_git_repository_bindings_workspace_id", "git_repository_bindings", ["workspace_id"])
    op.create_index("ix_git_repository_bindings_created_by", "git_repository_bindings", ["created_by"])
    op.create_index("ix_git_repository_bindings_provider", "git_repository_bindings", ["provider"])
    op.create_index("ix_git_repository_bindings_status", "git_repository_bindings", ["status"])
    op.create_index("ix_git_bindings_project_provider", "git_repository_bindings", ["project_id", "provider"])

    op.create_table(
        "git_operations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("binding_id", sa.String(36), sa.ForeignKey("git_repository_bindings.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_by", sa.String(36), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("branch", sa.String(120), nullable=False, server_default=""),
        sa.Column("head_before", sa.String(64), nullable=False, server_default=""),
        sa.Column("head_after", sa.String(64), nullable=False, server_default=""),
        sa.Column("remote_head", sa.String(64), nullable=False, server_default=""),
        sa.Column("summary", sa.JSON(), nullable=False),
        sa.Column("failure_reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_git_operations_binding_id", "git_operations", ["binding_id"])
    op.create_index("ix_git_operations_project_id", "git_operations", ["project_id"])
    op.create_index("ix_git_operations_created_by", "git_operations", ["created_by"])
    op.create_index("ix_git_operations_kind", "git_operations", ["kind"])
    op.create_index("ix_git_operations_status", "git_operations", ["status"])
    op.create_index("ix_git_operations_binding_created", "git_operations", ["binding_id", "created_at"])
    op.create_index("ix_git_operations_project_kind", "git_operations", ["project_id", "kind"])


def downgrade():
    op.drop_table("git_operations")
    op.drop_table("git_repository_bindings")
