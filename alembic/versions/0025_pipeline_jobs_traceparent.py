"""Add pipeline_jobs.traceparent for OTEL context propagation across worker boundary

Revision ID: 0025
Revises: 0024
Create Date: 2026-04-25

"""

import sqlalchemy as sa

from alembic import op

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Nullable so already-queued legacy jobs (created before this column
    # existed) keep dispatching; the worker logs a warning and runs without
    # trace continuity in that case.
    op.add_column(
        "pipeline_jobs",
        sa.Column("traceparent", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("pipeline_jobs", "traceparent")
