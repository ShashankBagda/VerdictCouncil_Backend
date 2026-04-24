"""Add draft/extracting/awaiting_intake_confirmation to casestatus,
create documentkind enum, add cases.intake_extraction and documents.kind.

Supports the new docs-as-source-of-truth intake flow: judge uploads typed
documents against a draft case, the extractor proposes fields, and the
judge confirms before the pipeline starts.

Revision ID: 0021
Revises: 0020
Create Date: 2026-04-23
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None

_NEW_CASESTATUS_VALUES = (
    "draft",
    "extracting",
    "awaiting_intake_confirmation",
)

_DOCUMENTKIND_VALUES = (
    "notice_of_traffic_offence",
    "charge_sheet",
    "evidence_bundle",
    "in_car_camera",
    "medical_report",
    "letter_of_mitigation",
    "other",
)


def upgrade() -> None:
    # 1. Additive enum values on casestatus. IF NOT EXISTS keeps the
    #    migration idempotent for environments where a prior partial run
    #    already added them. Downgrade is a no-op (Postgres cannot DROP VALUE).
    for value in _NEW_CASESTATUS_VALUES:
        op.execute(f"ALTER TYPE casestatus ADD VALUE IF NOT EXISTS '{value}'")

    # 2. Additive enum value on pipelinejobtype for the intake extractor worker.
    op.execute("ALTER TYPE pipelinejobtype ADD VALUE IF NOT EXISTS 'intake_extraction'")

    # 3. Create documentkind enum.
    documentkind = postgresql.ENUM(*_DOCUMENTKIND_VALUES, name="documentkind")
    documentkind.create(op.get_bind(), checkfirst=True)

    # 3. cases.intake_extraction — proposed fields + citations from extractor.
    op.add_column(
        "cases",
        sa.Column("intake_extraction", postgresql.JSONB(), nullable=True),
    )

    # 4. documents.kind — typed slot. Existing rows default to 'other'.
    op.add_column(
        "documents",
        sa.Column(
            "kind",
            postgresql.ENUM(*_DOCUMENTKIND_VALUES, name="documentkind", create_type=False),
            nullable=False,
            server_default="other",
        ),
    )


def downgrade() -> None:
    op.drop_column("documents", "kind")
    op.drop_column("cases", "intake_extraction")

    postgresql.ENUM(name="documentkind").drop(op.get_bind(), checkfirst=True)

    # casestatus enum values are not removed: Postgres has no DROP VALUE and
    # rebuilding the type would require a table swap. Unused values are inert.
