"""add media center and image safety policy

Revision ID: ab82f4d91c30
Revises: f17c2e8a9104
"""
from alembic import op
import sqlalchemy as sa

revision = "ab82f4d91c30"
down_revision = "f17c2e8a9104"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "image_safety_policies",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(length=24), nullable=False, server_default="draft"),
        sa.Column("name", sa.String(length=160), nullable=False, server_default="Image Safety Policy"),
        sa.Column("superprompt", sa.Text(), nullable=False, server_default=""),
        sa.Column("rules", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.String(length=36), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("published_by", sa.String(length=36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("version"),
    )
    op.create_index("ix_image_safety_policies_version", "image_safety_policies", ["version"], unique=True)
    op.create_index("ix_image_safety_policies_state", "image_safety_policies", ["state"])
    op.create_index("ix_image_safety_policy_state_version", "image_safety_policies", ["state", "version"])
    op.create_index("ix_image_safety_policies_created_by", "image_safety_policies", ["created_by"])
    op.create_index("ix_image_safety_policies_published_by", "image_safety_policies", ["published_by"])

    op.create_table(
        "image_policy_test_cases",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("policy_id", sa.String(length=36), sa.ForeignKey("image_safety_policies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("expected", sa.String(length=16), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_image_policy_test_cases_policy_id", "image_policy_test_cases", ["policy_id"])
    op.create_index("ix_image_policy_test_cases_active", "image_policy_test_cases", ["active"])
    op.create_index("ix_image_policy_tests_policy_created", "image_policy_test_cases", ["policy_id", "created_at"])

    with op.batch_alter_table("image_qa_events") as batch:
        batch.add_column(sa.Column("qa_type", sa.String(length=24), nullable=False, server_default="deterministic"))
        batch.create_index("ix_image_qa_events_qa_type", ["qa_type"])

    with op.batch_alter_table("image_generations") as batch:
        batch.add_column(sa.Column("safety_policy_id", sa.String(length=36), nullable=True))
        batch.add_column(sa.Column("safety_status", sa.String(length=24), nullable=False, server_default="unchecked"))
        batch.add_column(sa.Column("delivery_status", sa.String(length=24), nullable=False, server_default="active"))
        batch.add_column(sa.Column("moderation_note", sa.Text(), nullable=False, server_default=""))
        batch.create_foreign_key("fk_image_generations_safety_policy", "image_safety_policies", ["safety_policy_id"], ["id"], ondelete="SET NULL")
        batch.create_index("ix_image_generations_safety_policy_id", ["safety_policy_id"])
        batch.create_index("ix_image_generations_safety_status", ["safety_status"])
        batch.create_index("ix_image_generations_delivery_status", ["delivery_status"])


def downgrade() -> None:
    with op.batch_alter_table("image_generations") as batch:
        batch.drop_index("ix_image_generations_delivery_status")
        batch.drop_index("ix_image_generations_safety_status")
        batch.drop_index("ix_image_generations_safety_policy_id")
        batch.drop_constraint("fk_image_generations_safety_policy", type_="foreignkey")
        batch.drop_column("moderation_note")
        batch.drop_column("delivery_status")
        batch.drop_column("safety_status")
        batch.drop_column("safety_policy_id")
    with op.batch_alter_table("image_qa_events") as batch:
        batch.drop_index("ix_image_qa_events_qa_type")
        batch.drop_column("qa_type")
    op.drop_index("ix_image_policy_tests_policy_created", table_name="image_policy_test_cases")
    op.drop_index("ix_image_policy_test_cases_active", table_name="image_policy_test_cases")
    op.drop_index("ix_image_policy_test_cases_policy_id", table_name="image_policy_test_cases")
    op.drop_table("image_policy_test_cases")
    op.drop_index("ix_image_safety_policies_published_by", table_name="image_safety_policies")
    op.drop_index("ix_image_safety_policies_created_by", table_name="image_safety_policies")
    op.drop_index("ix_image_safety_policy_state_version", table_name="image_safety_policies")
    op.drop_index("ix_image_safety_policies_state", table_name="image_safety_policies")
    op.drop_index("ix_image_safety_policies_version", table_name="image_safety_policies")
    op.drop_table("image_safety_policies")
