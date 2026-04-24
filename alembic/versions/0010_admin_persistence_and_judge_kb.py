"""Admin persistence and per-judge knowledge base vector store.

Revision ID: 0010
Revises: 0009
Create Date: 2026-04-21
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "admin_events",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "actor_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("payload", JSONB, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_admin_events_actor_id", "admin_events", ["actor_id"])
    op.create_index("ix_admin_events_action", "admin_events", ["action"])
    op.create_index("ix_admin_events_created_at", "admin_events", ["created_at"])

    op.create_table(
        "system_config",
        sa.Column("key", sa.String(100), primary_key=True),
        sa.Column("value", JSONB, nullable=False),
        sa.Column(
            "updated_by",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=True,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    op.add_column(
        "users",
        sa.Column("knowledge_base_vector_store_id", sa.String(255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("users", "knowledge_base_vector_store_id")

    op.drop_table("system_config")

    op.drop_index("ix_admin_events_created_at", table_name="admin_events")
    op.drop_index("ix_admin_events_action", table_name="admin_events")
    op.drop_index("ix_admin_events_actor_id", table_name="admin_events")
    op.drop_table("admin_events")
