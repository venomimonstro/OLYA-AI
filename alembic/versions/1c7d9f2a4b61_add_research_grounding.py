"""add research grounding

Revision ID: 1c7d9f2a4b61
Revises: 078da198df85
Create Date: 2026-09-01
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "1c7d9f2a4b61"
down_revision: Union[str, Sequence[str], None] = "078da198df85"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "research_sources",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=True),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("final_url", sa.Text(), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("http_status", sa.Integer(), nullable=False),
        sa.Column("media_type", sa.String(length=120), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_research_sources_user_id", "research_sources", ["user_id"], unique=False)
    op.create_index("ix_research_sources_project_id", "research_sources", ["project_id"], unique=False)
    op.create_index("ix_research_sources_content_sha256", "research_sources", ["content_sha256"], unique=False)
    op.create_index("ix_research_sources_status", "research_sources", ["status"], unique=False)
    op.create_index("ix_research_sources_fetched_at", "research_sources", ["fetched_at"], unique=False)
    op.create_index("ix_research_sources_user_fetched", "research_sources", ["user_id", "fetched_at"], unique=False)
    op.create_index("ix_research_sources_project_fetched", "research_sources", ["project_id", "fetched_at"], unique=False)
    op.create_index("ix_research_sources_hash", "research_sources", ["content_sha256"], unique=False)

    op.create_table(
        "source_evidence",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("source_id", sa.String(length=36), nullable=False),
        sa.Column("created_by", sa.String(length=36), nullable=False),
        sa.Column("claim", sa.Text(), nullable=False),
        sa.Column("excerpt", sa.Text(), nullable=False),
        sa.Column("state", sa.String(length=24), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_id"], ["research_sources.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_source_evidence_source_id", "source_evidence", ["source_id"], unique=False)
    op.create_index("ix_source_evidence_created_by", "source_evidence", ["created_by"], unique=False)
    op.create_index("ix_source_evidence_state", "source_evidence", ["state"], unique=False)
    op.create_index("ix_source_evidence_source_created", "source_evidence", ["source_id", "created_at"], unique=False)
    op.create_index("ix_source_evidence_creator", "source_evidence", ["created_by", "created_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_source_evidence_creator", table_name="source_evidence")
    op.drop_index("ix_source_evidence_source_created", table_name="source_evidence")
    op.drop_index("ix_source_evidence_state", table_name="source_evidence")
    op.drop_index("ix_source_evidence_created_by", table_name="source_evidence")
    op.drop_index("ix_source_evidence_source_id", table_name="source_evidence")
    op.drop_table("source_evidence")

    op.drop_index("ix_research_sources_hash", table_name="research_sources")
    op.drop_index("ix_research_sources_project_fetched", table_name="research_sources")
    op.drop_index("ix_research_sources_user_fetched", table_name="research_sources")
    op.drop_index("ix_research_sources_fetched_at", table_name="research_sources")
    op.drop_index("ix_research_sources_status", table_name="research_sources")
    op.drop_index("ix_research_sources_content_sha256", table_name="research_sources")
    op.drop_index("ix_research_sources_project_id", table_name="research_sources")
    op.drop_index("ix_research_sources_user_id", table_name="research_sources")
    op.drop_table("research_sources")
