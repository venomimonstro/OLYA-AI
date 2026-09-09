"""allow image reference bytes to be detached on deletion

Revision ID: f60a93c7d511
Revises: f59e8d4a2c10
Create Date: 2026-09-09
"""

from alembic import op
import sqlalchemy as sa

revision = "f60a93c7d511"
down_revision = "f59e8d4a2c10"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("image_references") as batch:
        batch.alter_column("blob_id", existing_type=sa.String(length=36), nullable=True)


def downgrade() -> None:
    # Downgrade cannot reconstruct bytes that were intentionally deleted. Refuse
    # to manufacture invalid FK values; operators must restore a pre-upgrade
    # backup if they need to go behind this privacy boundary.
    bind = op.get_bind()
    missing = bind.execute(sa.text("SELECT COUNT(*) FROM image_references WHERE blob_id IS NULL")).scalar_one()
    if int(missing or 0):
        raise RuntimeError("Cannot downgrade: deleted image references have detached blobs")
    with op.batch_alter_table("image_references") as batch:
        batch.alter_column("blob_id", existing_type=sa.String(length=36), nullable=False)
