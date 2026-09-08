"""add image editing references and durable edit ledger

Revision ID: f59e8d4a2c10
Revises: f53b21e7c4a0
Create Date: 2026-09-08
"""

from alembic import op
import sqlalchemy as sa

revision = "f59e8d4a2c10"
down_revision = "f53b21e7c4a0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "image_references",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", sa.String(length=36), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=True),
        sa.Column("blob_id", sa.String(length=36), sa.ForeignKey("image_blobs.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("kind", sa.String(length=24), nullable=False, server_default="edit_source"),
        sa.Column("original_name", sa.String(length=240), nullable=False, server_default="image.png"),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="ready"),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_image_references_user_id", "image_references", ["user_id"])
    op.create_index("ix_image_references_project_id", "image_references", ["project_id"])
    op.create_index("ix_image_references_user_created", "image_references", ["user_id", "created_at"])
    op.create_index("ix_image_references_project_created", "image_references", ["project_id", "created_at"])
    op.create_index("ix_image_references_blob", "image_references", ["blob_id"])
    op.create_index("ix_image_references_status", "image_references", ["status"])
    op.create_index("ix_image_references_created_at", "image_references", ["created_at"])

    op.create_table(
        "image_edit_requests",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("generation_id", sa.String(length=36), sa.ForeignKey("image_generations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", sa.String(length=36), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=True),
        sa.Column("source_reference_id", sa.String(length=36), sa.ForeignKey("image_references.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("mask_reference_id", sa.String(length=36), sa.ForeignKey("image_references.id", ondelete="SET NULL"), nullable=True),
        sa.Column("identity_reference_id", sa.String(length=36), sa.ForeignKey("image_references.id", ondelete="SET NULL"), nullable=True),
        sa.Column("mode", sa.String(length=32), nullable=False, server_default="auto"),
        sa.Column("instruction", sa.Text(), nullable=False),
        sa.Column("preserve_identity", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("preserve_outside_mask", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("strict_quality", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="queued"),
        sa.Column("plan", sa.JSON(), nullable=False),
        sa.Column("qa_summary", sa.JSON(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("generation_id", name="uq_image_edit_generation"),
    )
    op.create_index("ix_image_edit_requests_generation_id", "image_edit_requests", ["generation_id"], unique=True)
    op.create_index("ix_image_edit_requests_user_id", "image_edit_requests", ["user_id"])
    op.create_index("ix_image_edit_requests_project_id", "image_edit_requests", ["project_id"])
    op.create_index("ix_image_edits_user_created", "image_edit_requests", ["user_id", "created_at"])
    op.create_index("ix_image_edits_project_created", "image_edit_requests", ["project_id", "created_at"])
    op.create_index("ix_image_edits_status", "image_edit_requests", ["status"])
    op.create_index("ix_image_edits_source", "image_edit_requests", ["source_reference_id"])
    op.create_index("ix_image_edit_requests_created_at", "image_edit_requests", ["created_at"])


def downgrade() -> None:
    op.drop_table("image_edit_requests")
    op.drop_table("image_references")
