"""Remove verdict machinery and rename deliberations to hearing_analyses.

Responsible AI refactor: the system supports judges for hearing preparation
only. AI verdict recommendations induce automation bias and cross into judicial
decision-making territory. This migration removes all verdict-related DB state
and renames deliberation artefacts to reflect their true purpose.

Changes:
  - DROP TABLE verdicts (and its dependent FK constraints)
  - UPDATE cases SET status='closed' WHERE status IN ('decided', 'rejected')
  - Rebuild casestatus PostgreSQL enum without decided/rejected
  - RENAME TABLE deliberations → hearing_analyses
  - RENAME TABLE what_if_verdicts → what_if_results
  - Rename columns in what_if_results: original_verdict→original_analysis,
    modified_verdict→modified_analysis, verdict_changed→analysis_changed
  - DROP TYPE recommendationtype (was only used by verdicts)

Revision ID: 0016
Revises: 0013
Create Date: 2026-04-22
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0016"
down_revision = "0013"
branch_labels = None
depends_on = None

_NEW_STATUSES = (
    "pending",
    "processing",
    "ready_for_review",
    "escalated",
    "closed",
    "failed",
    "failed_retryable",
)

_OLD_STATUSES = (
    "pending",
    "processing",
    "ready_for_review",
    "decided",
    "rejected",
    "escalated",
    "closed",
    "failed",
    "failed_retryable",
)


def upgrade() -> None:
    conn = op.get_bind()

    # ------------------------------------------------------------------ #
    # 1. Drop verdicts table (cascades FK constraints from amendments)
    # ------------------------------------------------------------------ #
    op.drop_table("verdicts")

    # ------------------------------------------------------------------ #
    # 2. Map terminal verdict statuses → closed before rebuilding enum
    # ------------------------------------------------------------------ #
    conn.execute(
        sa.text("UPDATE cases SET status = 'closed' WHERE status IN ('decided', 'rejected')")
    )

    # ------------------------------------------------------------------ #
    # 3. Rebuild casestatus enum without decided/rejected.
    #    PostgreSQL does not support DROP VALUE, so we use rename-create-
    #    alter-drop (same approach as migration 0018 for userrole).
    # ------------------------------------------------------------------ #
    conn.execute(sa.text("ALTER TYPE casestatus RENAME TO casestatus_old"))
    conn.execute(
        sa.text(
            "CREATE TYPE casestatus AS ENUM (" + ", ".join(f"'{v}'" for v in _NEW_STATUSES) + ")"
        )
    )
    conn.execute(
        sa.text(
            "ALTER TABLE cases ALTER COLUMN status TYPE casestatus USING status::text::casestatus"
        )
    )
    conn.execute(sa.text("DROP TYPE casestatus_old"))

    # ------------------------------------------------------------------ #
    # 4. Drop RecommendationType enum (was only used by verdicts)
    # ------------------------------------------------------------------ #
    conn.execute(sa.text("DROP TYPE IF EXISTS recommendationtype"))

    # ------------------------------------------------------------------ #
    # 5. Rename deliberations → hearing_analyses
    # ------------------------------------------------------------------ #
    op.rename_table("deliberations", "hearing_analyses")

    # ------------------------------------------------------------------ #
    # 6. Rename what_if_verdicts → what_if_results + column renames
    # ------------------------------------------------------------------ #
    op.rename_table("what_if_verdicts", "what_if_results")
    op.alter_column("what_if_results", "original_verdict", new_column_name="original_analysis")
    op.alter_column("what_if_results", "modified_verdict", new_column_name="modified_analysis")
    op.alter_column("what_if_results", "verdict_changed", new_column_name="analysis_changed")


def downgrade() -> None:
    conn = op.get_bind()

    # Reverse what_if table renames
    op.alter_column("what_if_results", "analysis_changed", new_column_name="verdict_changed")
    op.alter_column("what_if_results", "modified_analysis", new_column_name="modified_verdict")
    op.alter_column("what_if_results", "original_analysis", new_column_name="original_verdict")
    op.rename_table("what_if_results", "what_if_verdicts")

    # Reverse deliberations rename
    op.rename_table("hearing_analyses", "deliberations")

    # Restore casestatus enum with decided/rejected
    conn.execute(sa.text("ALTER TYPE casestatus RENAME TO casestatus_new"))
    conn.execute(
        sa.text(
            "CREATE TYPE casestatus AS ENUM (" + ", ".join(f"'{v}'" for v in _OLD_STATUSES) + ")"
        )
    )
    conn.execute(
        sa.text(
            "ALTER TABLE cases ALTER COLUMN status TYPE casestatus USING status::text::casestatus"
        )
    )
    conn.execute(sa.text("DROP TYPE casestatus_new"))

    # Recreate verdicts table (minimal structure — amendment FKs not restored)
    op.create_table(
        "verdicts",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "case_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("cases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("recommendation_type", sa.String(50), nullable=False),
        sa.Column("recommended_outcome", sa.Text, nullable=False),
        sa.Column("sentence", sa.dialects.postgresql.JSONB),
        sa.Column("confidence_score", sa.Integer),
        sa.Column("alternative_outcomes", sa.dialects.postgresql.JSONB),
        sa.Column("fairness_report", sa.dialects.postgresql.JSONB),
        sa.Column("amendment_of", sa.dialects.postgresql.UUID(as_uuid=True)),
        sa.Column("amendment_reason", sa.Text),
        sa.Column("amended_by", sa.dialects.postgresql.UUID(as_uuid=True)),
        sa.Column("created_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
    )
