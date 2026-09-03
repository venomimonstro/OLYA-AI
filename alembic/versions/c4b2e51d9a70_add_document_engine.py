"""add document engine

Revision ID: c4b2e51d9a70
Revises: a31f9c77d2e4
"""
from alembic import op
import sqlalchemy as sa

revision = "c4b2e51d9a70"
down_revision = "a31f9c77d2e4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "document_artifacts",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", sa.String(length=36), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=True),
        sa.Column("title", sa.String(length=240), nullable=False),
        sa.Column("logical_name", sa.String(length=240), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("current_revision", sa.Integer(), nullable=False),
        sa.Column("released_revision", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_document_artifacts_user_id", "document_artifacts", ["user_id"])
    op.create_index("ix_document_artifacts_project_id", "document_artifacts", ["project_id"])
    op.create_index("ix_document_artifacts_user_created", "document_artifacts", ["user_id", "created_at"])
    op.create_index("ix_document_artifacts_project_created", "document_artifacts", ["project_id", "created_at"])
    op.create_index("ix_document_artifacts_status", "document_artifacts", ["status"])

    op.create_table(
        "document_revisions",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("artifact_id", sa.String(length=36), sa.ForeignKey("document_artifacts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("spec", sa.JSON(), nullable=False),
        sa.Column("docx_path", sa.Text(), nullable=False),
        sa.Column("docx_sha256", sa.String(length=64), nullable=False),
        sa.Column("pdf_path", sa.Text(), nullable=False),
        sa.Column("pdf_sha256", sa.String(length=64), nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=False),
        sa.Column("qa_status", sa.String(length=24), nullable=False),
        sa.Column("qa_report", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.String(length=36), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("artifact_id", "revision", name="uq_document_artifact_revision"),
    )
    op.create_index("ix_document_revisions_artifact_id", "document_revisions", ["artifact_id"])
    op.create_index("ix_document_revisions_artifact_revision", "document_revisions", ["artifact_id", "revision"])
    op.create_index("ix_document_revisions_docx_sha256", "document_revisions", ["docx_sha256"])
    op.create_index("ix_document_revisions_pdf_sha256", "document_revisions", ["pdf_sha256"])
    op.create_index("ix_document_revisions_qa_status", "document_revisions", ["qa_status"])
    op.create_index("ix_document_revisions_created_by", "document_revisions", ["created_by"])

    op.create_table(
        "document_qa_events",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("revision_id", sa.String(length=36), sa.ForeignKey("document_revisions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("gate", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_document_qa_events_revision_id", "document_qa_events", ["revision_id"])
    op.create_index("ix_document_qa_events_gate", "document_qa_events", ["gate"])
    op.create_index("ix_document_qa_events_status", "document_qa_events", ["status"])
    op.create_index("ix_document_qa_revision_created", "document_qa_events", ["revision_id", "created_at"])


def downgrade() -> None:
    op.drop_table("document_qa_events")
    op.drop_table("document_revisions")
    op.drop_table("document_artifacts")
