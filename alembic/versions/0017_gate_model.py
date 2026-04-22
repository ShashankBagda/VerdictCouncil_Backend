"""Add gated pipeline: gate_state/judicial_decision/pages columns + 4 new statuses.

Adds 4 gate-pause statuses to casestatus enum (awaiting_review_gate1-4),
gate_state and judicial_decision JSONB columns to cases, and pages JSONB to
documents. Also adds gate_run to pipelinejobtype enum and migrates any
legacy escalated rows to ready_for_review.

Revision ID: 0017
Revises: 0016
Create Date: 2026-04-23
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None

OLD_CASESTATUS_VALUES = (
    "pending",
    "processing",
    "ready_for_review",
    "escalated",
    "closed",
    "failed",
    "failed_retryable",
)
NEW_CASESTATUS_VALUES = (
    "pending",
    "processing",
    "ready_for_review",
    "escalated",
    "closed",
    "failed",
    "failed_retryable",
    "awaiting_review_gate1",
    "awaiting_review_gate2",
    "awaiting_review_gate3",
    "awaiting_review_gate4",
)

OLD_JOBTYPE_VALUES = ("case_pipeline", "whatif_scenario", "stability_computation")
NEW_JOBTYPE_VALUES = ("case_pipeline", "whatif_scenario", "stability_computation", "gate_run")

OLD_CASESTATUS = sa.Enum(*OLD_CASESTATUS_VALUES, name="casestatus")
NEW_CASESTATUS = sa.Enum(*NEW_CASESTATUS_VALUES, name="casestatus")

OLD_JOBTYPE = sa.Enum(*OLD_JOBTYPE_VALUES, name="pipelinejobtype")
NEW_JOBTYPE = sa.Enum(*NEW_JOBTYPE_VALUES, name="pipelinejobtype")


def upgrade() -> None:
    conn = op.get_bind()

    # ------------------------------------------------------------------ #
    # 1. Migrate legacy escalated rows → ready_for_review
    #    With the simplified single-judge model, escalation is no longer
    #    a valid workflow state. Existing escalated rows are surfaced as
    #    ready for review so the judge can action them.
    # ------------------------------------------------------------------ #
    conn.execute(
        sa.text("UPDATE cases SET status = 'ready_for_review' WHERE status = 'escalated'")
    )

    # ------------------------------------------------------------------ #
    # 2. Rebuild casestatus enum with 4 new gate-pause values.
    #    PostgreSQL does not support DROP VALUE, so we swap the type using
    #    the same create-alter-drop pattern as migration 0016.
    # ------------------------------------------------------------------ #
    NEW_CASESTATUS.create(conn, checkfirst=True)
    conn.execute(
        sa.text(
            "ALTER TABLE cases "
            "ALTER COLUMN status TYPE casestatus USING status::text::casestatus"
        )
    )
    OLD_CASESTATUS.drop(conn, checkfirst=False)

    # ------------------------------------------------------------------ #
    # 3. Rebuild pipelinejobtype enum to add gate_run.
    # ------------------------------------------------------------------ #
    NEW_JOBTYPE.create(conn, checkfirst=True)
    conn.execute(
        sa.text(
            "ALTER TABLE pipeline_jobs "
            "ALTER COLUMN job_type TYPE pipelinejobtype USING job_type::text::pipelinejobtype"
        )
    )
    OLD_JOBTYPE.drop(conn, checkfirst=False)

    # ------------------------------------------------------------------ #
    # 4. Add JSONB columns to cases and documents.
    # ------------------------------------------------------------------ #
    op.add_column("cases", sa.Column("gate_state", sa.dialects.postgresql.JSONB(), nullable=True))
    op.add_column(
        "cases", sa.Column("judicial_decision", sa.dialects.postgresql.JSONB(), nullable=True)
    )
    op.add_column(
        "documents", sa.Column("pages", sa.dialects.postgresql.JSONB(), nullable=True)
    )


def downgrade() -> None:
    conn = op.get_bind()

    # Remove added columns
    op.drop_column("documents", "pages")
    op.drop_column("cases", "judicial_decision")
    op.drop_column("cases", "gate_state")

    # Restore pipelinejobtype enum without gate_run
    OLD_JOBTYPE.create(conn, checkfirst=True)
    conn.execute(
        sa.text(
            "UPDATE pipeline_jobs SET job_type = 'case_pipeline' WHERE job_type = 'gate_run'"
        )
    )
    conn.execute(
        sa.text(
            "ALTER TABLE pipeline_jobs "
            "ALTER COLUMN job_type TYPE pipelinejobtype USING job_type::text::pipelinejobtype"
        )
    )
    NEW_JOBTYPE.drop(conn, checkfirst=False)

    # Restore casestatus enum without gate-pause values
    conn.execute(
        sa.text(
            "UPDATE cases SET status = 'processing' "
            "WHERE status IN ("
            "  'awaiting_review_gate1', 'awaiting_review_gate2',"
            "  'awaiting_review_gate3', 'awaiting_review_gate4'"
            ")"
        )
    )
    OLD_CASESTATUS.create(conn, checkfirst=True)
    conn.execute(
        sa.text(
            "ALTER TABLE cases "
            "ALTER COLUMN status TYPE casestatus USING status::text::casestatus"
        )
    )
    NEW_CASESTATUS.drop(conn, checkfirst=False)
