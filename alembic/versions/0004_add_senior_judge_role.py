"""Add senior_judge value to userrole enum.

Revision ID: 0004
Revises: 0003
Create Date: 2026-04-19
"""

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            ALTER TYPE userrole ADD VALUE 'senior_judge';
        EXCEPTION
            WHEN duplicate_object THEN NULL;
        END
        $$;
        """
    )


def downgrade() -> None:
    # Enum value removal is non-trivial in PostgreSQL and intentionally skipped.
    pass
