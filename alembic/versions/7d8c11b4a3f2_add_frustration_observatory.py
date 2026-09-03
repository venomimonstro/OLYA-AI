"""add frustration observatory

Revision ID: 7d8c11b4a3f2
Revises: 6b7e91af21c3
Create Date: 2026-09-01
"""
from typing import Sequence, Union
import sqlalchemy as sa
from alembic import op

revision: str = "7d8c11b4a3f2"
down_revision: Union[str, Sequence[str], None] = "6b7e91af21c3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "frustration_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id", ondelete="SET NULL"), nullable=True),
        sa.Column("conversation_id", sa.String(36), sa.ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True),
        sa.Column("request_id", sa.String(36), nullable=True),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False, server_default="warning"),
        sa.Column("source", sa.String(24), nullable=False, server_default="server"),
        sa.Column("metrics", sa.JSON(), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False, server_default=""),
        sa.Column("resolved", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    for name, cols in [
        ("ix_frustration_events_user_id", ["user_id"]), ("ix_frustration_events_project_id", ["project_id"]),
        ("ix_frustration_events_conversation_id", ["conversation_id"]), ("ix_frustration_events_request_id", ["request_id"]),
        ("ix_frustration_events_kind", ["kind"]), ("ix_frustration_events_severity", ["severity"]),
        ("ix_frustration_events_fingerprint", ["fingerprint"]), ("ix_frustration_events_resolved", ["resolved"]),
        ("ix_frustration_events_created_at", ["created_at"]), ("ix_frustration_user_created", ["user_id", "created_at"]),
        ("ix_frustration_kind_created", ["kind", "created_at"]), ("ix_frustration_severity_created", ["severity", "created_at"]),
        ("ix_frustration_request", ["request_id"]),
    ]:
        op.create_index(name, "frustration_events", cols)


def downgrade() -> None:
    for name in ["ix_frustration_request","ix_frustration_severity_created","ix_frustration_kind_created","ix_frustration_user_created","ix_frustration_events_created_at","ix_frustration_events_resolved","ix_frustration_events_fingerprint","ix_frustration_events_severity","ix_frustration_events_kind","ix_frustration_events_request_id","ix_frustration_events_conversation_id","ix_frustration_events_project_id","ix_frustration_events_user_id"]:
        op.drop_index(name, table_name="frustration_events")
    op.drop_table("frustration_events")
