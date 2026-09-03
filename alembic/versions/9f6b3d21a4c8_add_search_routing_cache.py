"""add search routing cache and provider stats

Revision ID: 9f6b3d21a4c8
Revises: 8d4a1c2f0b77
Create Date: 2026-09-01
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "9f6b3d21a4c8"
down_revision: Union[str, None] = "8d4a1c2f0b77"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "search_query_cache",
        sa.Column("cache_key", sa.String(length=64), primary_key=True),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("country", sa.String(length=8), nullable=False, server_default=""),
        sa.Column("language", sa.String(length=16), nullable=False, server_default=""),
        sa.Column("provider_set", sa.String(length=200), nullable=False, server_default=""),
        sa.Column("results", sa.JSON(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("hit_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_search_cache_created", "search_query_cache", ["created_at"])
    op.create_table(
        "search_provider_stats",
        sa.Column("provider", sa.String(length=64), primary_key=True),
        sa.Column("request_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("success_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failure_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_latency_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_latency_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.String(length=500), nullable=False, server_default=""),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("search_provider_stats")
    op.drop_index("ix_search_cache_created", table_name="search_query_cache")
    op.drop_table("search_query_cache")
