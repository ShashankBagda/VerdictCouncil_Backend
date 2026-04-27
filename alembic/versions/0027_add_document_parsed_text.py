"""Cache parsed document text on documents.parsed_text — Q2.1.

Adds the JSONB column the upload-time `document_parse` worker writes
to and the runner-side hydrator (Q2.2) reads from. NULL on existing
rows; legacy documents fall through to the runner-side fallback.

Also adds the `document_parse` value to the `pipelinejobtype` enum
so the upload handler can enqueue per-document parse jobs.

Revision ID: 0027
Revises: 0026
Create Date: 2026-04-26
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "documents",
        sa.Column("parsed_text", postgresql.JSONB(), nullable=True),
    )
    op.execute("ALTER TYPE pipelinejobtype ADD VALUE IF NOT EXISTS 'document_parse'")


def downgrade() -> None:
    op.drop_column("documents", "parsed_text")
    # Postgres has no DROP VALUE on enums; the unused 'document_parse'
    # value is inert. Matches the precedent in 0021.
