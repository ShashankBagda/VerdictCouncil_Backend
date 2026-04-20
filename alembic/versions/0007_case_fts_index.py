"""Add tsvector GIN index on cases.description for full-text search.

Revision ID: 0007
Revises: 0006
Create Date: 2026-04-16
"""

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # GIN index over a functional expression — Postgres recomputes the tsvector
    # on writes but storage stays low (no generated column needed).
    op.execute(
        "CREATE INDEX ix_cases_description_fts "
        "ON cases USING gin (to_tsvector('simple', coalesce(description, '')))"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_cases_description_fts")
