"""add answer quality audits

Revision ID: 078da198df85
Revises: bff29ea4eab8
Create Date: 2026-09-01
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "078da198df85"
down_revision: Union[str, Sequence[str], None] = "bff29ea4eab8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "answer_audits",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=True),
        sa.Column("conversation_id", sa.String(length=36), nullable=True),
        sa.Column("request_id", sa.String(length=36), nullable=False),
        sa.Column("verification_mode", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("checks", sa.JSON(), nullable=False),
        sa.Column("warnings", sa.JSON(), nullable=False),
        sa.Column("critic", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_answer_audits_user_id", "answer_audits", ["user_id"], unique=False)
    op.create_index("ix_answer_audits_project_id", "answer_audits", ["project_id"], unique=False)
    op.create_index("ix_answer_audits_conversation_id", "answer_audits", ["conversation_id"], unique=False)
    op.create_index("ix_answer_audits_request_id", "answer_audits", ["request_id"], unique=False)
    op.create_index("ix_answer_audits_status", "answer_audits", ["status"], unique=False)
    op.create_index("ix_answer_audits_user_created", "answer_audits", ["user_id", "created_at"], unique=False)
    op.create_index("ix_answer_audits_request", "answer_audits", ["request_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_answer_audits_request", table_name="answer_audits")
    op.drop_index("ix_answer_audits_user_created", table_name="answer_audits")
    op.drop_index("ix_answer_audits_status", table_name="answer_audits")
    op.drop_index("ix_answer_audits_request_id", table_name="answer_audits")
    op.drop_index("ix_answer_audits_conversation_id", table_name="answer_audits")
    op.drop_index("ix_answer_audits_project_id", table_name="answer_audits")
    op.drop_index("ix_answer_audits_user_id", table_name="answer_audits")
    op.drop_table("answer_audits")
