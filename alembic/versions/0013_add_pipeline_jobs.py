"""Add pipeline_jobs transactional outbox table.

Revision ID: 0013
Revises: 0012
Create Date: 2026-04-22

Additive DDL only — no writers, no readers in this migration. A follow-up
PR replaces `fastapi.BackgroundTasks` with an arq worker that polls this
table as a transactional outbox so a post-commit crash can no longer
strand a case in `processing` status with no dispatched job. Landing the
schema as its own release lets the table exist in every environment
before any producer/consumer code references it, so the arq swap can be
done in a single atomic code change with zero table-does-not-exist
windows.
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


JOB_TYPE_ENUM = sa.Enum(
    "case_pipeline",
    "whatif_scenario",
    "stability_computation",
    name="pipelinejobtype",
)

JOB_STATUS_ENUM = sa.Enum(
    "pending",
    "dispatched",
    "completed",
    "failed",
    name="pipelinejobstatus",
)


def upgrade() -> None:
    JOB_TYPE_ENUM.create(op.get_bind(), checkfirst=True)
    JOB_STATUS_ENUM.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "pipeline_jobs",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "case_id",
            UUID(as_uuid=True),
            sa.ForeignKey("cases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("job_type", JOB_TYPE_ENUM, nullable=False),
        sa.Column("target_id", UUID(as_uuid=True), nullable=True),
        sa.Column(
            "status",
            JOB_STATUS_ENUM,
            nullable=False,
            server_default="pending",
        ),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("payload", JSONB(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_index(
        "ix_pipeline_jobs_status_created_at",
        "pipeline_jobs",
        ["status", "created_at"],
    )
    op.create_index(
        "ix_pipeline_jobs_case_id",
        "pipeline_jobs",
        ["case_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_pipeline_jobs_case_id", table_name="pipeline_jobs")
    op.drop_index("ix_pipeline_jobs_status_created_at", table_name="pipeline_jobs")
    op.drop_table("pipeline_jobs")
    JOB_STATUS_ENUM.drop(op.get_bind(), checkfirst=True)
    JOB_TYPE_ENUM.drop(op.get_bind(), checkfirst=True)
