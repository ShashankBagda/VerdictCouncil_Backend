from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import Base, UUIDPrimaryKeyMixin


class CalibrationRecord(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "calibration_records"

    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cases.id", ondelete="CASCADE"), nullable=False
    )
    judge_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    ai_recommendation_type: Mapped[str | None] = mapped_column(String(100))
    ai_confidence_score: Mapped[int | None] = mapped_column(Integer)
    judge_decision: Mapped[str] = mapped_column(String(50), nullable=False)
    judge_modification_summary: Mapped[str | None] = mapped_column(Text)
    divergence_score: Mapped[float] = mapped_column(Float, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
