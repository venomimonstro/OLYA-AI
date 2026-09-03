"""add image qa and storage optimization

Revision ID: f17c2e8a9104
Revises: e91a7d63bc20
"""
from alembic import op
import sqlalchemy as sa

revision = "f17c2e8a9104"
down_revision = "e91a7d63bc20"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("image_generations") as batch:
        batch.add_column(sa.Column("qa_status", sa.String(length=24), nullable=False, server_default="pending"))
        batch.add_column(sa.Column("repair_attempts", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("preferred_blob_id", sa.String(length=36), nullable=True))
        batch.create_foreign_key("fk_image_generations_preferred_blob", "image_blobs", ["preferred_blob_id"], ["id"], ondelete="SET NULL")
        batch.create_index("ix_image_generations_qa_status", ["qa_status"])
        batch.create_index("ix_image_generations_preferred_blob_id", ["preferred_blob_id"])

    op.create_table(
        "image_variants",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("source_blob_id", sa.String(length=36), sa.ForeignKey("image_blobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("blob_id", sa.String(length=36), sa.ForeignKey("image_blobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("codec", sa.String(length=16), nullable=False),
        sa.Column("quality", sa.Integer(), nullable=True),
        sa.Column("perceptual_error", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_image_variants_source_blob_id", "image_variants", ["source_blob_id"])
    op.create_index("ix_image_variants_blob_id", "image_variants", ["blob_id"])
    op.create_index("ix_image_variants_source_kind", "image_variants", ["source_blob_id", "kind"], unique=True)

    op.create_table(
        "image_qa_events",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("generation_id", sa.String(length=36), sa.ForeignKey("image_generations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("findings", sa.JSON(), nullable=False),
        sa.Column("metrics", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_image_qa_events_generation_id", "image_qa_events", ["generation_id"])
    op.create_index("ix_image_qa_events_status", "image_qa_events", ["status"])
    op.create_index("ix_image_qa_generation_created", "image_qa_events", ["generation_id", "created_at"])


def downgrade() -> None:
    op.drop_table("image_qa_events")
    op.drop_table("image_variants")
    with op.batch_alter_table("image_generations") as batch:
        batch.drop_index("ix_image_generations_preferred_blob_id")
        batch.drop_index("ix_image_generations_qa_status")
        batch.drop_constraint("fk_image_generations_preferred_blob", type_="foreignkey")
        batch.drop_column("preferred_blob_id")
        batch.drop_column("repair_attempts")
        batch.drop_column("qa_status")
