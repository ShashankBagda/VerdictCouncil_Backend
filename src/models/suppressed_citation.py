"""Suppressed citation record (Sprint 4 4.C4.1).

One row per citation dropped by the citation-provenance validator
(Sprint 3 3.B.5). Phase-keyed; `subagent` is meaningful only when
phase = 'research'. `reason` mirrors the `SuppressionReason` Pydantic
literal.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import Base

_PHASE_VALUES = ("intake", "research", "synthesis", "audit")
_SUBAGENT_VALUES = ("evidence", "facts", "witnesses", "law")
_REASONS = (
    "no_source_match",
    "low_score",
    "expired_statute",
    "out_of_jurisdiction",
)


def _phase_check(name: str) -> str:
    quoted = ",".join(f"'{v}'" for v in _PHASE_VALUES)
    return f"{name} IN ({quoted})"


def _subagent_check(col: str) -> str:
    quoted = ",".join(f"'{v}'" for v in _SUBAGENT_VALUES)
    return f"{col} IS NULL OR {col} IN ({quoted})"


def _reason_check() -> str:
    quoted = ",".join(f"'{v}'" for v in _REASONS)
    return f"reason IN ({quoted})"


class SuppressedCitationRecord(Base):
    __tablename__ = "suppressed_citations"
    __table_args__ = (
        CheckConstraint(_phase_check("phase"), name="suppressed_citations_phase_check"),
        CheckConstraint(_subagent_check("subagent"), name="suppressed_citations_subagent_check"),
        CheckConstraint(
            "subagent IS NULL OR phase = 'research'",
            name="suppressed_citations_subagent_only_for_research",
        ),
        CheckConstraint(_reason_check(), name="suppressed_citations_reason_check"),
        Index("suppressed_citations_case_idx", "case_id"),
        Index("suppressed_citations_run_idx", "run_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cases.id", ondelete="CASCADE"),
        nullable=False,
    )
    run_id: Mapped[str] = mapped_column(Text, nullable=False)
    phase: Mapped[str] = mapped_column(Text, nullable=False)
    subagent: Mapped[str | None] = mapped_column(Text)
    citation_text: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
