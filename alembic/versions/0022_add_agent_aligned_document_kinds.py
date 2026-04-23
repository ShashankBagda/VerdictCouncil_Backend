"""Add agent-aligned document kinds to documentkind enum.

Adds three sub-types the downstream agents (case-processing,
evidence-analysis) already classify against — so the judge can upload
directly into the right kind at intake instead of dropping everything
into evidence_bundle and forcing the agent to infer sub-type from
content.

Additive only. `in_car_camera` is left in the enum for back-compat with
rows created before image/video uploads were dropped from the frontend.

Revision ID: 0022
Revises: 0021
Create Date: 2026-04-23
"""

from __future__ import annotations

from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None

_NEW_DOCUMENTKIND_VALUES = (
    "police_report",
    "witness_statement",
    "speed_camera_record",
)


def upgrade() -> None:
    # Additive enum values. IF NOT EXISTS makes the migration idempotent.
    # Postgres cannot DROP VALUE, so downgrade is a no-op.
    for value in _NEW_DOCUMENTKIND_VALUES:
        op.execute(f"ALTER TYPE documentkind ADD VALUE IF NOT EXISTS '{value}'")


def downgrade() -> None:
    # Postgres has no DROP VALUE. Added kinds are inert if the app
    # stops writing them, so the downgrade is a no-op.
    pass
