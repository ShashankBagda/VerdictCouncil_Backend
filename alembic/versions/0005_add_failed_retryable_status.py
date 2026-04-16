"""Add failed_retryable value to casestatus enum.

Revision ID: 0005
Revises: 0004
Create Date: 2026-04-16
"""

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE casestatus ADD VALUE IF NOT EXISTS 'failed_retryable'")


def downgrade() -> None:
    # PostgreSQL does not support removing enum values directly.
    # Downgrade is a no-op — the value will remain unused if this migration is rolled back.
    pass
