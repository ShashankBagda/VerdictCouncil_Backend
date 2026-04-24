from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import Base, UUIDPrimaryKeyMixin


class PipelineJobType(str, enum.Enum):
    case_pipeline = "case_pipeline"
    whatif_scenario = "whatif_scenario"
    stability_computation = "stability_computation"
    gate_run = "gate_run"
    # Pre-pipeline intake: read judge-uploaded typed documents and propose
    # structured fields (parties, offence_code, title, description, filed_date,
    # claim_amount) for the judge to confirm before the 9-agent pipeline runs.
    intake_extraction = "intake_extraction"


class PipelineJobStatus(str, enum.Enum):
    pending = "pending"
    dispatched = "dispatched"
    completed = "completed"
    failed = "failed"


class PipelineJob(UUIDPrimaryKeyMixin, Base):
    """Transactional-outbox row for pipeline dispatch.

    Writers INSERT a row in the same tx that flips the case / scenario /
    stability status, so the outbox can never drift from persisted state.
    A separate arq dispatcher polls `(status, created_at)` and claims
    rows by flipping `pending` → `dispatched`, guaranteeing at-least-once
    enqueue even if the web process crashes post-commit.
    """

    __tablename__ = "pipeline_jobs"

    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cases.id", ondelete="CASCADE"), nullable=False
    )
    job_type: Mapped[PipelineJobType] = mapped_column(Enum(PipelineJobType, name="pipelinejobtype"), nullable=False)
    target_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    status: Mapped[PipelineJobStatus] = mapped_column(
        Enum(PipelineJobStatus, name="pipelinejobstatus"),
        nullable=False,
        server_default=PipelineJobStatus.pending.value,
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
