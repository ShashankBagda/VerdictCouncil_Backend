"""Re-add senior_judge role to userrole enum.

Revision ID: 0020
Revises: 0019
Create Date: 2026-04-23
"""

from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Re-add senior_judge to the enum
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
    # To truly downgrade, you would need manual intervention.
    pass
