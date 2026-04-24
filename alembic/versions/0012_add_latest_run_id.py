"""Add latest_run_id to cases; backfill from pipeline_checkpoints.

Revision ID: 0012
Revises: 0011
Create Date: 2026-04-22

The What-If pipeline needs a stable anchor to rehydrate the terminal
CaseState for a completed case. `latest_run_id` is written by
`persist_case_results` at the end of each successful pipeline run and
read by the what-if / stability endpoints via `load_case_state`.

Backfill rationale: cases completed before this migration have no
latest_run_id pointer, which would silently break what-if for every
historical case. We point them at the most recently updated checkpoint
row, which for a terminal case IS the terminal state (per-run UPSERT
means one row per run).
"""

import sqlalchemy as sa

from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("cases", sa.Column("latest_run_id", sa.String(length=36), nullable=True))
    op.execute(
        """
        UPDATE cases
        SET latest_run_id = sub.run_id
        FROM (
            SELECT DISTINCT ON (case_id) case_id, run_id
            FROM pipeline_checkpoints
            ORDER BY case_id, updated_at DESC
        ) AS sub
        WHERE cases.id = sub.case_id
          AND cases.latest_run_id IS NULL
        """
    )


def downgrade() -> None:
    op.drop_column("cases", "latest_run_id")
