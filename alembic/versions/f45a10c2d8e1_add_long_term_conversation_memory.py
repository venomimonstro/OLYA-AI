"""add long-term conversation memory

Revision ID: f45a10c2d8e1
Revises: f38f09c1b437
"""
from alembic import op
import sqlalchemy as sa

revision = "f45a10c2d8e1"
down_revision = "f38f09c1b437"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "conversation_memories",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("conversation_id", sa.String(36), sa.ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=True),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("memory_key", sa.String(96), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("keywords", sa.JSON(), nullable=False),
        sa.Column("source_role", sa.String(16), nullable=False, server_default="user"),
        sa.Column("source_message_id", sa.String(36), sa.ForeignKey("messages.id", ondelete="SET NULL"), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("conversation_id", "kind", "memory_key", name="uq_conversation_memory_kind_key"),
    )
    op.create_index("ix_conversation_memory_conversation_kind", "conversation_memories", ["conversation_id", "kind"])
    op.create_index("ix_conversation_memory_project_kind", "conversation_memories", ["project_id", "kind"])
    op.create_index("ix_conversation_memory_updated", "conversation_memories", ["updated_at"])
    op.create_index("ix_conversation_memories_conversation_id", "conversation_memories", ["conversation_id"])
    op.create_index("ix_conversation_memories_project_id", "conversation_memories", ["project_id"])
    op.create_index("ix_conversation_memories_kind", "conversation_memories", ["kind"])
    op.create_index("ix_conversation_memories_source_message_id", "conversation_memories", ["source_message_id"])
    op.create_index("ix_conversation_memories_created_at", "conversation_memories", ["created_at"])


def downgrade() -> None:
    op.drop_table("conversation_memories")
