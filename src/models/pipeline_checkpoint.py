"""SQLAlchemy model for the `pipeline_checkpoints` table.

The mesh runner persists a mid-pipeline checkpoint after every agent
completes. The row is upserted via raw SQL in `src/db/pipeline_state.py`
(no ORM reads/writes go through this model) — but declaring the table
here ensures `scripts.reset_db` (which rebuilds the schema from
`Base.metadata.create_all` and then stamps alembic to head) actually
creates it. Without this model the table exists only in migration
0009, which `reset_db` skips, and checkpoint upserts fail with
`relation "pipeline_checkpoints" does not exist`.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Index, PrimaryKeyConstraint, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import Base


class PipelineCheckpoint(Base):
    __tablename__ = "pipeline_checkpoints"

    case_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    run_id: Mapped[str] = mapped_column(Text, nullable=False)
    agent_name: Mapped[str] = mapped_column(Text, nullable=False)
    case_state: Mapped[dict] = mapped_column(JSONB, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        PrimaryKeyConstraint("case_id", "run_id", name="pk_pipeline_checkpoints"),
        Index("ix_pipeline_checkpoints_case_id", "case_id"),
    )
