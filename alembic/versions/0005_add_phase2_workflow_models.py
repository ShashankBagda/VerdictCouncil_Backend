"""Add Phase 2 workflow models and amendment fields.

Revision ID: 0005
Revises: 0004
Create Date: 2026-04-19
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    reopen_request_status = sa.Enum(
        "pending",
        "approved",
        "rejected",
        name="reopenrequeststatus",
        create_type=False,
    )
    reopen_request_status.create(op.get_bind(), checkfirst=True)

    op.add_column("verdicts", sa.Column("amendment_of", UUID(as_uuid=True), nullable=True))
    op.add_column("verdicts", sa.Column("amendment_reason", sa.Text(), nullable=True))
    op.add_column("verdicts", sa.Column("amended_by", UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        "fk_verdicts_amendment_of_verdicts",
        "verdicts",
        "verdicts",
        ["amendment_of"],
        ["id"],
    )
    op.create_foreign_key(
        "fk_verdicts_amended_by_users",
        "verdicts",
        "users",
        ["amended_by"],
        ["id"],
    )

    op.create_table(
        "hearing_notes",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("case_id", UUID(as_uuid=True), sa.ForeignKey("cases.id", ondelete="CASCADE"), nullable=False),
        sa.Column("judge_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("section_reference", sa.String(255), nullable=True),
        sa.Column("note_type", sa.String(50), nullable=False),
        sa.Column("is_locked", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_hearing_notes_case_id", "hearing_notes", ["case_id"])
    op.create_index("ix_hearing_notes_judge_id", "hearing_notes", ["judge_id"])

    op.create_table(
        "reopen_requests",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("case_id", UUID(as_uuid=True), sa.ForeignKey("cases.id", ondelete="CASCADE"), nullable=False),
        sa.Column("requested_by", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("reason", sa.String(50), nullable=False),
        sa.Column("justification", sa.Text(), nullable=False),
        sa.Column("status", reopen_request_status, nullable=False, server_default="pending"),
        sa.Column("reviewed_by", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("review_notes", sa.Text(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_reopen_requests_case_id", "reopen_requests", ["case_id"])
    op.create_index("ix_reopen_requests_status", "reopen_requests", ["status"])


def downgrade() -> None:
    op.drop_index("ix_reopen_requests_status", table_name="reopen_requests")
    op.drop_index("ix_reopen_requests_case_id", table_name="reopen_requests")
    op.drop_table("reopen_requests")

    op.drop_index("ix_hearing_notes_judge_id", table_name="hearing_notes")
    op.drop_index("ix_hearing_notes_case_id", table_name="hearing_notes")
    op.drop_table("hearing_notes")

    op.drop_constraint("fk_verdicts_amended_by_users", "verdicts", type_="foreignkey")
    op.drop_constraint("fk_verdicts_amendment_of_verdicts", "verdicts", type_="foreignkey")
    op.drop_column("verdicts", "amended_by")
    op.drop_column("verdicts", "amendment_reason")
    op.drop_column("verdicts", "amendment_of")

    reopen_request_status = sa.Enum(
        "pending",
        "approved",
        "rejected",
        name="reopenrequeststatus",
        create_type=False,
    )
    reopen_request_status.drop(op.get_bind(), checkfirst=True)
