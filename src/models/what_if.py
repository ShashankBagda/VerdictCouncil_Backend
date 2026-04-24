"""What-If scenario models for Contestable Judgment Mode."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.base import Base, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from src.models.case import Case
    from src.models.user import User


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class ModificationType(str, enum.Enum):
    fact_toggle = "fact_toggle"
    evidence_exclusion = "evidence_exclusion"
    witness_credibility = "witness_credibility"
    legal_interpretation = "legal_interpretation"


class ScenarioStatus(str, enum.Enum):
    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


class StabilityClassification(str, enum.Enum):
    stable = "stable"
    moderately_sensitive = "moderately_sensitive"
    highly_sensitive = "highly_sensitive"


class StabilityStatus(str, enum.Enum):
    pending = "pending"
    computing = "computing"
    completed = "completed"
    failed = "failed"


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class WhatIfScenario(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "what_if_scenarios"

    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cases.id", ondelete="CASCADE"), nullable=False
    )
    original_run_id: Mapped[str] = mapped_column(String(255), nullable=False)
    scenario_run_id: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    modification_type: Mapped[ModificationType] = mapped_column(
        Enum(ModificationType), nullable=False
    )
    modification_description: Mapped[str | None] = mapped_column(Text)
    modification_payload: Mapped[dict | None] = mapped_column(JSONB)
    status: Mapped[ScenarioStatus] = mapped_column(
        Enum(ScenarioStatus), nullable=False, server_default=ScenarioStatus.pending.value
    )
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Relationships
    case: Mapped[Case] = relationship()
    created_by_user: Mapped[User] = relationship()
    result: Mapped[WhatIfResult | None] = relationship(
        back_populates="scenario", uselist=False, cascade="all, delete-orphan"
    )


class WhatIfResult(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "what_if_results"

    scenario_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("what_if_scenarios.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )
    original_analysis: Mapped[dict | None] = mapped_column(JSONB)
    modified_analysis: Mapped[dict | None] = mapped_column(JSONB)
    diff_view: Mapped[dict | None] = mapped_column(JSONB)
    analysis_changed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Relationships
    scenario: Mapped[WhatIfScenario] = relationship(back_populates="result")


class StabilityScore(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "stability_scores"

    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cases.id", ondelete="CASCADE"), nullable=False
    )
    run_id: Mapped[str] = mapped_column(String(255), nullable=False)
    score: Mapped[int] = mapped_column(Integer, nullable=False)
    classification: Mapped[StabilityClassification] = mapped_column(
        Enum(StabilityClassification), nullable=False
    )
    perturbation_count: Mapped[int] = mapped_column(Integer, nullable=False)
    perturbations_held: Mapped[int] = mapped_column(Integer, nullable=False)
    perturbation_details: Mapped[dict | None] = mapped_column(JSONB)
    status: Mapped[StabilityStatus] = mapped_column(
        Enum(StabilityStatus), nullable=False, server_default=StabilityStatus.pending.value
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Relationships
    case: Mapped[Case] = relationship()
