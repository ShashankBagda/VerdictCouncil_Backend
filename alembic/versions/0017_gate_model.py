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

_OLD_CASESTATUS_VALUES = (
    "pending",
    "processing",
    "ready_for_review",
    "escalated",
    "closed",
    "failed",
    "failed_retryable",
)
_NEW_CASESTATUS_VALUES = (
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

_OLD_JOBTYPE_VALUES = ("case_pipeline", "whatif_scenario", "stability_computation")
_NEW_JOBTYPE_VALUES = ("case_pipeline", "whatif_scenario", "stability_computation", "gate_run")


def upgrade() -> None:
    conn = op.get_bind()

    # ------------------------------------------------------------------ #
    # 1. Migrate legacy escalated rows → ready_for_review
    # ------------------------------------------------------------------ #
    conn.execute(sa.text("UPDATE cases SET status = 'ready_for_review' WHERE status = 'escalated'"))

    # ------------------------------------------------------------------ #
    # 2. Rebuild casestatus enum with 4 new gate-pause values.
    #    Uses rename-create-alter-drop (same pattern as migration 0018).
    # ------------------------------------------------------------------ #
    conn.execute(sa.text("ALTER TYPE casestatus RENAME TO casestatus_old"))
    conn.execute(
        sa.text(
            "CREATE TYPE casestatus AS ENUM ("
            + ", ".join(f"'{v}'" for v in _NEW_CASESTATUS_VALUES)
            + ")"
        )
    )
    conn.execute(
        sa.text(
            "ALTER TABLE cases ALTER COLUMN status TYPE casestatus USING status::text::casestatus"
        )
    )
    conn.execute(sa.text("DROP TYPE casestatus_old"))

    # ------------------------------------------------------------------ #
    # 3. Rebuild pipelinejobtype enum to add gate_run.
    # ------------------------------------------------------------------ #
    conn.execute(sa.text("ALTER TYPE pipelinejobtype RENAME TO pipelinejobtype_old"))
    conn.execute(
        sa.text(
            "CREATE TYPE pipelinejobtype AS ENUM ("
            + ", ".join(f"'{v}'" for v in _NEW_JOBTYPE_VALUES)
            + ")"
        )
    )
    conn.execute(
        sa.text(
            "ALTER TABLE pipeline_jobs "
            "ALTER COLUMN job_type TYPE pipelinejobtype USING job_type::text::pipelinejobtype"
        )
    )
    conn.execute(sa.text("DROP TYPE pipelinejobtype_old"))

    # ------------------------------------------------------------------ #
    # 4. Add JSONB columns to cases and documents.
    # ------------------------------------------------------------------ #
    op.add_column("cases", sa.Column("gate_state", sa.dialects.postgresql.JSONB(), nullable=True))
    op.add_column(
        "cases", sa.Column("judicial_decision", sa.dialects.postgresql.JSONB(), nullable=True)
    )
    op.add_column("documents", sa.Column("pages", sa.dialects.postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    conn = op.get_bind()

    # Remove added columns
    op.drop_column("documents", "pages")
    op.drop_column("cases", "judicial_decision")
    op.drop_column("cases", "gate_state")

    # Restore pipelinejobtype enum without gate_run
    conn.execute(
        sa.text("UPDATE pipeline_jobs SET job_type = 'case_pipeline' WHERE job_type = 'gate_run'")
    )
    conn.execute(sa.text("ALTER TYPE pipelinejobtype RENAME TO pipelinejobtype_new"))
    conn.execute(
        sa.text(
            "CREATE TYPE pipelinejobtype AS ENUM ("
            + ", ".join(f"'{v}'" for v in _OLD_JOBTYPE_VALUES)
            + ")"
        )
    )
    conn.execute(
        sa.text(
            "ALTER TABLE pipeline_jobs "
            "ALTER COLUMN job_type TYPE pipelinejobtype USING job_type::text::pipelinejobtype"
        )
    )
    conn.execute(sa.text("DROP TYPE pipelinejobtype_new"))

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
    conn.execute(sa.text("ALTER TYPE casestatus RENAME TO casestatus_new"))
    conn.execute(
        sa.text(
            "CREATE TYPE casestatus AS ENUM ("
            + ", ".join(f"'{v}'" for v in _OLD_CASESTATUS_VALUES)
            + ")"
        )
    )
    conn.execute(
        sa.text(
            "ALTER TABLE cases ALTER COLUMN status TYPE casestatus USING status::text::casestatus"
        )
    )
    conn.execute(sa.text("DROP TYPE casestatus_new"))
