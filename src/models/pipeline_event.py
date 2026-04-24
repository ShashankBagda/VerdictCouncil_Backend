from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import Base, UUIDPrimaryKeyMixin


class PipelineEvent(UUIDPrimaryKeyMixin, Base):
    """Append-only log of every SSE event emitted during a pipeline run.

    Written by a fire-and-forget tee alongside every Redis publish so events
    can be replayed or queried offline without a live SSE stream.
    """

    __tablename__ = "pipeline_events"
    __table_args__ = (
        Index("ix_pipeline_events_case_ts", "case_id", "ts"),
        Index("ix_pipeline_events_payload_gin", "payload", postgresql_using="gin"),
    )

    case_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    agent: Mapped[str | None] = mapped_column(Text, nullable=True)
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
