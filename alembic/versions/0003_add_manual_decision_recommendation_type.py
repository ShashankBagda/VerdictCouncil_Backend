"""Add manual_decision value to recommendationtype enum.

Revision ID: 0003
Revises: 0002
Create Date: 2026-04-06
"""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE recommendationtype ADD VALUE IF NOT EXISTS 'manual_decision'")


def downgrade() -> None:
    # PostgreSQL does not support removing enum values directly.
    # Downgrade is a no-op — the value will remain unused if this migration is rolled back.
    pass
