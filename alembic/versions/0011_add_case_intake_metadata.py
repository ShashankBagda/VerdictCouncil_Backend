"""Add structured intake metadata to cases.

Revision ID: 0011
Revises: 0010
Create Date: 2026-04-22
"""

from alembic import op
import sqlalchemy as sa

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("cases", sa.Column("title", sa.String(length=255), nullable=True))
    op.add_column("cases", sa.Column("filed_date", sa.Date(), nullable=True))
    op.add_column("cases", sa.Column("claim_amount", sa.Float(), nullable=True))
    op.add_column(
        "cases",
        sa.Column(
            "consent_to_higher_claim_limit",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column("cases", sa.Column("offence_code", sa.String(length=100), nullable=True))
    op.create_index("ix_cases_filed_date", "cases", ["filed_date"])
    op.create_index("ix_cases_offence_code", "cases", ["offence_code"])


def downgrade() -> None:
    op.drop_index("ix_cases_offence_code", table_name="cases")
    op.drop_index("ix_cases_filed_date", table_name="cases")
    op.drop_column("cases", "offence_code")
    op.drop_column("cases", "consent_to_higher_claim_limit")
    op.drop_column("cases", "claim_amount")
    op.drop_column("cases", "filed_date")
    op.drop_column("cases", "title")
