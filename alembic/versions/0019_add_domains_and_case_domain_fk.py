"""Add domains, domain_documents tables and cases.domain_id FK.

Revision ID: 0019
Revises: 0018
Create Date: 2026-04-23
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None

# Seed rows must match the CaseDomain enum labels verbatim (case.py:30-32)
_SEED_DOMAINS = [
    ("small_claims", "Small Claims Tribunal", "Small Claims Tribunals Act jurisdiction"),
    ("traffic_violation", "Traffic Court", "Road Traffic Act jurisdiction"),
]


def upgrade() -> None:
    op.create_table(
        "domains",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("code", sa.String(100), nullable=False, unique=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("vector_store_id", sa.String(255), nullable=True, unique=True),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("created_by", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("provisioning_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("provisioning_attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
            nullable=False,
        ),
    )

    op.create_table(
        "domain_documents",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "domain_id",
            UUID(as_uuid=True),
            sa.ForeignKey("domains.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("openai_file_id", sa.String(255), nullable=True),
        sa.Column("sanitized_file_id", sa.String(255), nullable=True),
        sa.Column("filename", sa.String(500), nullable=False),
        sa.Column("mime_type", sa.String(100), nullable=True),
        sa.Column("size_bytes", sa.Integer, nullable=True),
        sa.Column("sanitized", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("error_reason", sa.Text, nullable=True),
        sa.Column("idempotency_key", UUID(as_uuid=True), nullable=False),
        sa.Column("uploaded_by", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "uploaded_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("idempotency_key", name="uq_domain_document_idempotency"),
    )

    # Add nullable domain_id FK to cases for dual-write parallel-run
    op.add_column(
        "cases", sa.Column("domain_id", UUID(as_uuid=True), sa.ForeignKey("domains.id"), nullable=True)
    )

    # Seed the two built-in domains with is_active=false, vector_store_id=NULL
    import uuid as _uuid

    for code, name, description in _SEED_DOMAINS:
        op.execute(
            sa.text(
                "INSERT INTO domains (id, code, name, description, is_active) "
                "VALUES (:id, :code, :name, :desc, false)"
            ).bindparams(id=str(_uuid.uuid4()), code=code, name=name, desc=description)
        )

    # Backfill domain_id in batches of 500 using CTE + FOR UPDATE SKIP LOCKED
    backfill_sql = sa.text(
        """
        WITH target AS (
            SELECT id FROM cases
            WHERE domain_id IS NULL
            FOR UPDATE SKIP LOCKED
            LIMIT 500
        )
        UPDATE cases
        SET domain_id = d.id
        FROM domains d, target t
        WHERE cases.id = t.id
          AND cases.domain::text = d.code
        """
    )
    conn = op.get_bind()
    while True:
        result = conn.execute(backfill_sql)
        if result.rowcount == 0:
            break


def downgrade() -> None:
    op.drop_column("cases", "domain_id")
    op.drop_table("domain_documents")
    op.drop_table("domains")
