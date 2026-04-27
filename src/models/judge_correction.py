"""Judge correction record (Sprint 4 4.C4.1).

One row per judge-issued correction at a gate. Phase-keyed (intake /
research / synthesis / audit) per the new 6-phase topology;
`subagent` is meaningful only when phase = 'research'. The audit_log
row that produced the correction can backreference it via
`audit_logs.judge_correction_id`.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

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
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.base import Base

if TYPE_CHECKING:
    from src.models.audit import AuditLog


_PHASE_VALUES = ("intake", "research", "synthesis", "audit")
_SUBAGENT_VALUES = ("evidence", "facts", "witnesses", "law")


def _phase_check(name: str) -> str:
    quoted = ",".join(f"'{v}'" for v in _PHASE_VALUES)
    return f"{name} IN ({quoted})"


def _subagent_check(col: str) -> str:
    quoted = ",".join(f"'{v}'" for v in _SUBAGENT_VALUES)
    return f"{col} IS NULL OR {col} IN ({quoted})"


class JudgeCorrection(Base):
    __tablename__ = "judge_corrections"
    __table_args__ = (
        CheckConstraint(_phase_check("phase"), name="judge_corrections_phase_check"),
        CheckConstraint(_subagent_check("subagent"), name="judge_corrections_subagent_check"),
        CheckConstraint(
            "subagent IS NULL OR phase = 'research'",
            name="judge_corrections_subagent_only_for_research",
        ),
        Index("judge_corrections_case_idx", "case_id"),
        Index("judge_corrections_run_idx", "run_id"),
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
    correction_text: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    audit_logs: Mapped[list[AuditLog]] = relationship(back_populates="judge_correction")
