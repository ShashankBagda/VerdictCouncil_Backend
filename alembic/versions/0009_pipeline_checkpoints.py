"""Add pipeline_checkpoints table for mesh runner mid-pipeline persistence.

Revision ID: 0009
Revises: 0008
Create Date: 2026-04-18
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "pipeline_checkpoints",
        sa.Column("case_id", UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", sa.Text(), nullable=False),
        sa.Column("agent_name", sa.Text(), nullable=False),
        sa.Column("case_state", JSONB(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.PrimaryKeyConstraint("case_id", "run_id", name="pk_pipeline_checkpoints"),
    )
    op.create_index(
        "ix_pipeline_checkpoints_case_id",
        "pipeline_checkpoints",
        ["case_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_pipeline_checkpoints_case_id", table_name="pipeline_checkpoints")
    op.drop_table("pipeline_checkpoints")
