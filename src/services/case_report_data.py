"""Shared CaseReportData struct and DB shaping for hearing-pack / PDF exports.

Item 7 (US-020 hearing pack zip) and Item 8 (US-027 PDF export) both
need to project a Case + every relation into a single serialisable
record. Centralise the projection here so the two exports cannot drift.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.models.case import Case


@dataclass
class CaseReportData:
    """Snapshot of a case and its relations, suitable for export rendering."""

    case_id: UUID
    domain: str
    status: str
    description: str | None
    created_at: datetime
    parties: list[dict[str, Any]] = field(default_factory=list)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    facts: list[dict[str, Any]] = field(default_factory=list)
    arguments: list[dict[str, Any]] = field(default_factory=list)
    fairness_report: dict[str, Any] | None = None
    decision_history: list[dict[str, Any]] = field(default_factory=list)


def _enum_to_str(value: Any) -> Any:
    """Render enum values as their string representation, leaving others as-is."""
    return value.value if hasattr(value, "value") else value


async def build_case_report_data(db: AsyncSession, case_id: UUID) -> CaseReportData | None:
    """Load a Case and shape it into a CaseReportData record.

    Returns ``None`` if the case does not exist. Eager-loads the same
    relations that ``GET /api/v1/cases/{case_id}`` does so the projection
    matches what callers see in the API.
    """
    result = await db.execute(
        select(Case)
        .where(Case.id == case_id)
        .options(
            selectinload(Case.parties),
            selectinload(Case.evidence),
            selectinload(Case.facts),
            selectinload(Case.arguments),
            selectinload(Case.audit_logs),
        )
    )
    case = result.scalar_one_or_none()
    if case is None:
        return None

    parties = [
        {
            "id": str(p.id),
            "name": p.name,
            "role": _enum_to_str(p.role),
            "contact_info": p.contact_info,
        }
        for p in case.parties
    ]

    evidence = [
        {
            "id": str(e.id),
            "evidence_type": _enum_to_str(e.evidence_type),
            "strength": _enum_to_str(e.strength),
            "admissibility_flags": e.admissibility_flags,
            "linked_claims": e.linked_claims,
        }
        for e in case.evidence
    ]

    facts = [
        {
            "id": str(f.id),
            "description": f.description,
            "event_date": f.event_date.isoformat() if f.event_date else None,
            "confidence": _enum_to_str(f.confidence),
            "status": _enum_to_str(f.status),
        }
        for f in case.facts
    ]

    arguments = [
        {
            "id": str(a.id),
            "side": _enum_to_str(a.side),
            "legal_basis": a.legal_basis,
            "weaknesses": a.weaknesses,
        }
        for a in case.arguments
    ]

    fairness_report: dict[str, Any] | None = None
    decision_history: list[dict[str, Any]] = []

    return CaseReportData(
        case_id=case.id,
        domain=_enum_to_str(case.domain),
        status=_enum_to_str(case.status),
        description=case.description,
        created_at=case.created_at,
        parties=parties,
        evidence=evidence,
        facts=facts,
        arguments=arguments,
        fairness_report=fairness_report,
        decision_history=decision_history,
    )
