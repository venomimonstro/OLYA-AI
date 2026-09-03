"""add image generation runtime

Revision ID: e91a7d63bc20
Revises: d72a9e41bc55
"""
from alembic import op
import sqlalchemy as sa

revision = "e91a7d63bc20"
down_revision = "d72a9e41bc55"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "image_blobs",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("media_type", sa.String(length=64), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("storage_path", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_image_blobs_sha256", "image_blobs", ["sha256"], unique=True)
    op.create_table(
        "image_generations",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", sa.String(length=36), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=True),
        sa.Column("job_id", sa.String(length=36), sa.ForeignKey("background_jobs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("blob_id", sa.String(length=36), sa.ForeignKey("image_blobs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("negative_prompt", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("backend", sa.String(length=64), nullable=False),
        sa.Column("model_name", sa.String(length=160), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("steps", sa.Integer(), nullable=False),
        sa.Column("seed", sa.Integer(), nullable=False),
        sa.Column("manifest", sa.JSON(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_image_generations_user_id", "image_generations", ["user_id"])
    op.create_index("ix_image_generations_project_id", "image_generations", ["project_id"])
    op.create_index("ix_image_generations_job_id", "image_generations", ["job_id"])
    op.create_index("ix_image_generations_blob_id", "image_generations", ["blob_id"])
    op.create_index("ix_image_generations_status", "image_generations", ["status"])
    op.create_index("ix_image_generations_user_created", "image_generations", ["user_id", "created_at"])
    op.create_index("ix_image_generations_project_created", "image_generations", ["project_id", "created_at"])
    op.create_index("ix_image_generations_status_created", "image_generations", ["status", "created_at"])


def downgrade() -> None:
    op.drop_table("image_generations")
    op.drop_table("image_blobs")
