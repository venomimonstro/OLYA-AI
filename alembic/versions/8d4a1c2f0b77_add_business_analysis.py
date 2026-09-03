"""add business analysis to research runs

Revision ID: 8d4a1c2f0b77
Revises: 5e2a914d7c11
Create Date: 2026-09-01
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "8d4a1c2f0b77"
down_revision: Union[str, Sequence[str], None] = "5e2a914d7c11"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("research_runs", sa.Column("business_analysis", sa.JSON(), nullable=False, server_default=sa.text("'{}'")))


def downgrade() -> None:
    op.drop_column("research_runs", "business_analysis")
