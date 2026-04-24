"""Add pipeline_events replay table

Revision ID: 0024
Revises: 0023
Create Date: 2026-04-24

"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "pipeline_events",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("case_id", UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("agent", sa.Text(), nullable=True),
        sa.Column(
            "ts",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("payload", JSONB(), nullable=False),
    )
    op.create_index("ix_pipeline_events_case_ts", "pipeline_events", ["case_id", "ts"])
    op.create_index(
        "ix_pipeline_events_payload_gin",
        "pipeline_events",
        ["payload"],
        postgresql_using="gin",
    )


def downgrade() -> None:
    op.drop_index("ix_pipeline_events_payload_gin", table_name="pipeline_events")
    op.drop_index("ix_pipeline_events_case_ts", table_name="pipeline_events")
    op.drop_table("pipeline_events")
