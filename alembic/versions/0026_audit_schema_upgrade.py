"""Audit log schema upgrade — Sprint 4 4.C4.1.

Adds judge_corrections + suppressed_citations tables and extends
audit_logs with trace/cost/source-id columns.

- Tables key on `phase` (intake/research/synthesis/audit) per the new
  6-phase topology, not legacy agent_name.
- subagent is meaningful only when phase = 'research' (CHECK enforces).
- All new tables use UUID FK to cases(id) ON DELETE CASCADE so child
  rows can never become orphaned or cross-tenant ambiguous.
- Plan reference §4.C4.1 named the migration "0025"; the slot was taken
  in Sprint 2 by 0025_pipeline_jobs_traceparent, so this is 0026.

Revision ID: 0026
Revises: 0025
Create Date: 2026-04-25
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


_PHASE_VALUES = ("intake", "research", "synthesis", "audit")
_SUBAGENT_VALUES = ("evidence", "facts", "witnesses", "law")
_SUPPRESSION_REASONS = (
    "no_source_match",
    "low_score",
    "expired_statute",
    "out_of_jurisdiction",
)


def _phase_check(name: str) -> str:
    quoted = ",".join(f"'{v}'" for v in _PHASE_VALUES)
    return f"{name} IN ({quoted})"


def _subagent_check(col: str) -> str:
    quoted = ",".join(f"'{v}'" for v in _SUBAGENT_VALUES)
    return f"{col} IS NULL OR {col} IN ({quoted})"


def _reason_check() -> str:
    quoted = ",".join(f"'{v}'" for v in _SUPPRESSION_REASONS)
    return f"reason IN ({quoted})"


def upgrade() -> None:
    op.create_table(
        "judge_corrections",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column(
            "case_id",
            UUID(as_uuid=True),
            sa.ForeignKey("cases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("run_id", sa.Text, nullable=False),
        sa.Column("phase", sa.Text, nullable=False),
        sa.Column("subagent", sa.Text, nullable=True),
        sa.Column("correction_text", sa.Text, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(_phase_check("phase"), name="judge_corrections_phase_check"),
        sa.CheckConstraint(_subagent_check("subagent"), name="judge_corrections_subagent_check"),
        sa.CheckConstraint(
            "subagent IS NULL OR phase = 'research'",
            name="judge_corrections_subagent_only_for_research",
        ),
    )
    op.create_index("judge_corrections_case_idx", "judge_corrections", ["case_id"])
    op.create_index("judge_corrections_run_idx", "judge_corrections", ["run_id"])

    op.create_table(
        "suppressed_citations",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column(
            "case_id",
            UUID(as_uuid=True),
            sa.ForeignKey("cases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("run_id", sa.Text, nullable=False),
        sa.Column("phase", sa.Text, nullable=False),
        sa.Column("subagent", sa.Text, nullable=True),
        sa.Column("citation_text", sa.Text, nullable=False),
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(_phase_check("phase"), name="suppressed_citations_phase_check"),
        sa.CheckConstraint(_subagent_check("subagent"), name="suppressed_citations_subagent_check"),
        sa.CheckConstraint(
            "subagent IS NULL OR phase = 'research'",
            name="suppressed_citations_subagent_only_for_research",
        ),
        sa.CheckConstraint(_reason_check(), name="suppressed_citations_reason_check"),
    )
    op.create_index("suppressed_citations_case_idx", "suppressed_citations", ["case_id"])
    op.create_index("suppressed_citations_run_idx", "suppressed_citations", ["run_id"])

    op.add_column("audit_logs", sa.Column("trace_id", sa.Text, nullable=True))
    op.add_column("audit_logs", sa.Column("span_id", sa.Text, nullable=True))
    op.add_column("audit_logs", sa.Column("retrieved_source_ids", JSONB, nullable=True))
    op.add_column("audit_logs", sa.Column("cost_usd", sa.Numeric(10, 6), nullable=True))
    op.add_column(
        "audit_logs",
        sa.Column(
            "redaction_applied",
            sa.Boolean,
            server_default=sa.text("false"),
            nullable=False,
        ),
    )
    op.add_column(
        "audit_logs",
        sa.Column(
            "judge_correction_id",
            sa.BigInteger,
            sa.ForeignKey("judge_corrections.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("audit_logs_trace_idx", "audit_logs", ["trace_id"])


def downgrade() -> None:
    op.drop_index("audit_logs_trace_idx", table_name="audit_logs")
    op.drop_column("audit_logs", "judge_correction_id")
    op.drop_column("audit_logs", "redaction_applied")
    op.drop_column("audit_logs", "cost_usd")
    op.drop_column("audit_logs", "retrieved_source_ids")
    op.drop_column("audit_logs", "span_id")
    op.drop_column("audit_logs", "trace_id")

    op.drop_index("suppressed_citations_run_idx", table_name="suppressed_citations")
    op.drop_index("suppressed_citations_case_idx", table_name="suppressed_citations")
    op.drop_table("suppressed_citations")

    op.drop_index("judge_corrections_run_idx", table_name="judge_corrections")
    op.drop_index("judge_corrections_case_idx", table_name="judge_corrections")
    op.drop_table("judge_corrections")
