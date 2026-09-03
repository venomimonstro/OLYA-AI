"""add research planner runs

Revision ID: 5e2a914d7c11
Revises: 1c7d9f2a4b61
Create Date: 2026-09-01
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "5e2a914d7c11"
down_revision: Union[str, Sequence[str], None] = "1c7d9f2a4b61"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "research_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=True),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("intent", sa.String(length=32), nullable=False),
        sa.Column("location", sa.String(length=200), nullable=False),
        sa.Column("category", sa.String(length=200), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("plan", sa.JSON(), nullable=False),
        sa.Column("discovery_results", sa.JSON(), nullable=False),
        sa.Column("visited_urls", sa.JSON(), nullable=False),
        sa.Column("max_queries", sa.Integer(), nullable=False),
        sa.Column("max_results", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_research_runs_user_id", "research_runs", ["user_id"])
    op.create_index("ix_research_runs_project_id", "research_runs", ["project_id"])
    op.create_index("ix_research_runs_intent", "research_runs", ["intent"])
    op.create_index("ix_research_runs_status", "research_runs", ["status"])
    op.create_index("ix_research_runs_user_created", "research_runs", ["user_id", "created_at"])
    op.create_index("ix_research_runs_project_status", "research_runs", ["project_id", "status"])


def downgrade() -> None:
    op.drop_index("ix_research_runs_project_status", table_name="research_runs")
    op.drop_index("ix_research_runs_user_created", table_name="research_runs")
    op.drop_index("ix_research_runs_status", table_name="research_runs")
    op.drop_index("ix_research_runs_intent", table_name="research_runs")
    op.drop_index("ix_research_runs_project_id", table_name="research_runs")
    op.drop_index("ix_research_runs_user_id", table_name="research_runs")
    op.drop_table("research_runs")
