from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.base import Base, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from src.models.case import Case
    from src.models.judge_correction import JudgeCorrection


class AuditLog(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "audit_logs"

    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cases.id", ondelete="CASCADE"), nullable=False
    )

    case: Mapped[Case] = relationship(back_populates="audit_logs")

    agent_name: Mapped[str] = mapped_column(String(100), nullable=False)
    action: Mapped[str] = mapped_column(String(255), nullable=False)
    input_payload: Mapped[dict | None] = mapped_column(JSONB)
    output_payload: Mapped[dict | None] = mapped_column(JSONB)
    system_prompt: Mapped[str | None] = mapped_column(Text)
    llm_response: Mapped[dict | None] = mapped_column(JSONB)
    tool_calls: Mapped[dict | None] = mapped_column(JSONB)
    model: Mapped[str | None] = mapped_column(String(100))
    token_usage: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Sprint 4 4.C4.1 — observability + cost + provenance + redaction columns.
    # Populated by 4.C4.2 once the migration ships.
    trace_id: Mapped[str | None] = mapped_column(Text)
    span_id: Mapped[str | None] = mapped_column(Text)
    retrieved_source_ids: Mapped[list[str] | None] = mapped_column(JSONB)
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))
    redaction_applied: Mapped[bool] = mapped_column(Boolean, server_default="false", nullable=False)
    judge_correction_id: Mapped[int | None] = mapped_column(
        ForeignKey("judge_corrections.id", ondelete="SET NULL")
    )
    judge_correction: Mapped[JudgeCorrection | None] = relationship(back_populates="audit_logs")
