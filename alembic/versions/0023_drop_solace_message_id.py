"""Drop solace_message_id from audit_logs

Revision ID: 0023
Revises: 0022
Create Date: 2026-04-24

"""

from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("audit_logs", "solace_message_id")


def downgrade() -> None:
    import sqlalchemy as sa

    op.add_column("audit_logs", sa.Column("solace_message_id", sa.String(255), nullable=True))
