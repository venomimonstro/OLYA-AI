"""add isolated project runtime

Revision ID: d4a71f8c20b1
Revises: c91e4a2b7f18
"""
from alembic import op
import sqlalchemy as sa
revision="d4a71f8c20b1"; down_revision="c91e4a2b7f18"; branch_labels=None; depends_on=None

def upgrade():
    op.create_table("project_runtimes",
        sa.Column("id",sa.String(36),primary_key=True), sa.Column("project_id",sa.String(36),sa.ForeignKey("projects.id",ondelete="CASCADE"),nullable=False),
        sa.Column("workspace_id",sa.String(36),sa.ForeignKey("code_workspaces.id",ondelete="CASCADE"),nullable=False), sa.Column("created_by",sa.String(36),sa.ForeignKey("users.id",ondelete="RESTRICT"),nullable=False),
        sa.Column("status",sa.String(24),nullable=False), sa.Column("runtime_root",sa.Text(),nullable=False), sa.Column("isolation_backend",sa.String(32),nullable=False),
        sa.Column("network_policy",sa.String(24),nullable=False), sa.Column("cpu_limit",sa.Float(),nullable=False), sa.Column("memory_limit_mb",sa.Integer(),nullable=False),
        sa.Column("disk_limit_mb",sa.Integer(),nullable=False), sa.Column("process_limit",sa.Integer(),nullable=False), sa.Column("manifest",sa.JSON(),nullable=False),
        sa.Column("created_at",sa.DateTime(timezone=True),nullable=False), sa.Column("updated_at",sa.DateTime(timezone=True),nullable=False), sa.UniqueConstraint("project_id",name="uq_project_runtime_project"))
    for c in ["project_id","workspace_id","created_by","status"]: op.create_index(f"ix_project_runtimes_{c}","project_runtimes",[c])
    op.create_index("ix_project_runtimes_status_updated","project_runtimes",["status","updated_at"])
    op.create_table("project_runtime_snapshots",
        sa.Column("id",sa.String(36),primary_key=True), sa.Column("runtime_id",sa.String(36),sa.ForeignKey("project_runtimes.id",ondelete="CASCADE"),nullable=False),
        sa.Column("created_by",sa.String(36),sa.ForeignKey("users.id",ondelete="RESTRICT"),nullable=False), sa.Column("state",sa.String(24),nullable=False),
        sa.Column("archive_path",sa.Text(),nullable=False), sa.Column("manifest_sha256",sa.String(64),nullable=False), sa.Column("file_count",sa.Integer(),nullable=False),
        sa.Column("total_bytes",sa.Integer(),nullable=False), sa.Column("manifest",sa.JSON(),nullable=False), sa.Column("created_at",sa.DateTime(timezone=True),nullable=False),
        sa.UniqueConstraint("runtime_id","manifest_sha256",name="uq_project_runtime_snapshot_hash"))
    for c in ["runtime_id","created_by","state","manifest_sha256"]: op.create_index(f"ix_project_runtime_snapshots_{c}","project_runtime_snapshots",[c])
    op.create_index("ix_project_runtime_snapshots_runtime_created","project_runtime_snapshots",["runtime_id","created_at"])
    op.create_table("project_runtime_secrets",
        sa.Column("id",sa.String(36),primary_key=True), sa.Column("runtime_id",sa.String(36),sa.ForeignKey("project_runtimes.id",ondelete="CASCADE"),nullable=False),
        sa.Column("name",sa.String(120),nullable=False), sa.Column("ciphertext",sa.Text(),nullable=False), sa.Column("created_by",sa.String(36),sa.ForeignKey("users.id",ondelete="RESTRICT"),nullable=False),
        sa.Column("created_at",sa.DateTime(timezone=True),nullable=False), sa.Column("updated_at",sa.DateTime(timezone=True),nullable=False), sa.UniqueConstraint("runtime_id","name",name="uq_project_runtime_secret_name"))
    for c in ["runtime_id","created_by"]: op.create_index(f"ix_project_runtime_secrets_{c}","project_runtime_secrets",[c])
    op.create_index("ix_project_runtime_secrets_runtime","project_runtime_secrets",["runtime_id","created_at"])

def downgrade():
    op.drop_table("project_runtime_secrets"); op.drop_table("project_runtime_snapshots"); op.drop_table("project_runtimes")
