"""add curated image self improvement

Revision ID: c91e4a2b7f18
Revises: ab82f4d91c30
"""
from alembic import op
import sqlalchemy as sa

revision = "c91e4a2b7f18"
down_revision = "ab82f4d91c30"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "image_feedback",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("generation_id", sa.String(length=36), sa.ForeignKey("image_generations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("rating", sa.Integer(), nullable=False),
        sa.Column("allow_training", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("generation_id", "user_id", name="uq_image_feedback_generation_user"),
    )
    op.create_index("ix_image_feedback_generation_id", "image_feedback", ["generation_id"])
    op.create_index("ix_image_feedback_user_id", "image_feedback", ["user_id"])
    op.create_index("ix_image_feedback_allow_training", "image_feedback", ["allow_training"])
    op.create_index("ix_image_feedback_generation_created", "image_feedback", ["generation_id", "created_at"])

    op.create_table(
        "image_training_examples",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("generation_id", sa.String(length=36), sa.ForeignKey("image_generations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("blob_id", sa.String(length=36), sa.ForeignKey("image_blobs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("label", sa.String(length=24), nullable=False),
        sa.Column("state", sa.String(length=24), nullable=False, server_default="candidate"),
        sa.Column("dedupe_key", sa.String(length=64), nullable=False),
        sa.Column("perceptual_hash", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("provenance", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.String(length=36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("reviewed_by", sa.String(length=36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("review_note", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("generation_id", name="uq_image_training_generation"),
        sa.UniqueConstraint("dedupe_key", name="uq_image_training_dedupe"),
    )
    for name in ["generation_id", "blob_id", "label", "state", "created_by", "reviewed_by"]:
        op.create_index(f"ix_image_training_examples_{name}", "image_training_examples", [name])
    op.create_index("ix_image_training_state_label", "image_training_examples", ["state", "label"])
    op.create_index("ix_image_training_examples_perceptual_hash", "image_training_examples", ["perceptual_hash"])

    op.create_table(
        "image_dataset_snapshots",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("state", sa.String(length=24), nullable=False, server_default="frozen"),
        sa.Column("manifest_sha256", sa.String(length=64), nullable=False),
        sa.Column("example_ids", sa.JSON(), nullable=False),
        sa.Column("positive_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("regression_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("manifest", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.String(length=36), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("manifest_sha256"),
    )
    op.create_index("ix_image_dataset_snapshots_state", "image_dataset_snapshots", ["state"])
    op.create_index("ix_image_dataset_snapshots_manifest_sha256", "image_dataset_snapshots", ["manifest_sha256"], unique=True)
    op.create_index("ix_image_dataset_snapshots_created_by", "image_dataset_snapshots", ["created_by"])
    op.create_index("ix_image_dataset_state_created", "image_dataset_snapshots", ["state", "created_at"])

    op.create_table(
        "image_improvement_runs",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("dataset_snapshot_id", sa.String(length=36), sa.ForeignKey("image_dataset_snapshots.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("component_type", sa.String(length=32), nullable=False),
        sa.Column("candidate_name", sa.String(length=160), nullable=False),
        sa.Column("artifact_sha256", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("baseline_metrics", sa.JSON(), nullable=False),
        sa.Column("candidate_metrics", sa.JSON(), nullable=False),
        sa.Column("state", sa.String(length=24), nullable=False, server_default="evaluated"),
        sa.Column("decision_reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_by", sa.String(length=36), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_image_improvement_runs_dataset_snapshot_id", "image_improvement_runs", ["dataset_snapshot_id"])
    op.create_index("ix_image_improvement_runs_component_type", "image_improvement_runs", ["component_type"])
    op.create_index("ix_image_improvement_runs_state", "image_improvement_runs", ["state"])
    op.create_index("ix_image_improvement_runs_created_by", "image_improvement_runs", ["created_by"])
    op.create_index("ix_image_improvement_state_created", "image_improvement_runs", ["state", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_image_improvement_state_created", table_name="image_improvement_runs")
    op.drop_index("ix_image_improvement_runs_created_by", table_name="image_improvement_runs")
    op.drop_index("ix_image_improvement_runs_state", table_name="image_improvement_runs")
    op.drop_index("ix_image_improvement_runs_component_type", table_name="image_improvement_runs")
    op.drop_index("ix_image_improvement_runs_dataset_snapshot_id", table_name="image_improvement_runs")
    op.drop_table("image_improvement_runs")
    op.drop_index("ix_image_dataset_state_created", table_name="image_dataset_snapshots")
    op.drop_index("ix_image_dataset_snapshots_created_by", table_name="image_dataset_snapshots")
    op.drop_index("ix_image_dataset_snapshots_manifest_sha256", table_name="image_dataset_snapshots")
    op.drop_index("ix_image_dataset_snapshots_state", table_name="image_dataset_snapshots")
    op.drop_table("image_dataset_snapshots")
    op.drop_index("ix_image_training_examples_perceptual_hash", table_name="image_training_examples")
    op.drop_index("ix_image_training_state_label", table_name="image_training_examples")
    for name in ["reviewed_by", "created_by", "state", "label", "blob_id", "generation_id"]:
        op.drop_index(f"ix_image_training_examples_{name}", table_name="image_training_examples")
    op.drop_table("image_training_examples")
    op.drop_index("ix_image_feedback_generation_created", table_name="image_feedback")
    op.drop_index("ix_image_feedback_allow_training", table_name="image_feedback")
    op.drop_index("ix_image_feedback_user_id", table_name="image_feedback")
    op.drop_index("ix_image_feedback_generation_id", table_name="image_feedback")
    op.drop_table("image_feedback")
