"""Hearing pack generation endpoint."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from src.api.deps import DBSession, require_role
from src.api.schemas.common import ErrorResponse
from src.api.schemas.hearing_notes import HearingPackResponse
from src.models.case import Case
from src.models.user import User, UserRole

router = APIRouter()


@router.post(
    "/{case_id}/hearing-pack",
    response_model=HearingPackResponse,
    operation_id="generate_hearing_pack",
    summary="Generate hearing pack",
    description="Assemble case materials into a consolidated hearing pack for judicial review.",
    responses={
        403: {"model": ErrorResponse, "description": "Insufficient permissions"},
        404: {"model": ErrorResponse, "description": "Case not found"},
    },
)
async def generate_hearing_pack(
    case_id: UUID,
    db: DBSession,
    current_user: User = require_role(UserRole.judge, UserRole.senior_judge),
) -> HearingPackResponse:
    result = await db.execute(
        select(Case)
        .where(Case.id == case_id)
        .options(
            selectinload(Case.parties),
            selectinload(Case.facts),
            selectinload(Case.evidence),
            selectinload(Case.witnesses),
            selectinload(Case.legal_rules),
            selectinload(Case.precedents),
            selectinload(Case.arguments),
            selectinload(Case.verdicts),
        )
    )
    case = result.scalar_one_or_none()
    if not case:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found")

    parties = [{"id": str(p.id), "name": p.name, "role": p.role.value} for p in case.parties]
    facts = [
        {
            "id": str(f.id),
            "description": f.description,
            "confidence": f.confidence.value if f.confidence else None,
            "status": f.status.value if f.status else None,
        }
        for f in case.facts
    ]
    evidence = [
        {
            "id": str(e.id),
            "evidence_type": e.evidence_type.value,
            "strength": e.strength.value if e.strength else None,
            "admissibility_flags": e.admissibility_flags,
        }
        for e in case.evidence
    ]
    witnesses = [
        {
            "id": str(w.id),
            "name": w.name,
            "role": w.role,
            "credibility_score": w.credibility_score,
        }
        for w in case.witnesses
    ]
    legal_framework = [
        {
            "id": str(r.id),
            "statute_name": r.statute_name,
            "section": r.section,
            "relevance_score": r.relevance_score,
        }
        for r in case.legal_rules
    ] + [
        {
            "id": str(p.id),
            "citation": p.citation,
            "court": p.court,
            "similarity_score": p.similarity_score,
        }
        for p in case.precedents
    ]

    arguments = {
        "claimant": [
            {
                "id": str(a.id),
                "legal_basis": a.legal_basis,
                "weaknesses": a.weaknesses,
            }
            for a in case.arguments
            if a.side.value == "claimant"
        ],
        "respondent": [
            {
                "id": str(a.id),
                "legal_basis": a.legal_basis,
                "weaknesses": a.weaknesses,
            }
            for a in case.arguments
            if a.side.value == "respondent"
        ],
    }

    current_verdict = None
    if case.verdicts:
        latest = sorted(case.verdicts, key=lambda v: v.id.hex)[-1]
        current_verdict = {
            "id": str(latest.id),
            "recommendation_type": latest.recommendation_type.value,
            "recommended_outcome": latest.recommended_outcome,
            "confidence_score": latest.confidence_score,
            "amendment_of": str(latest.amendment_of) if latest.amendment_of else None,
        }

    return HearingPackResponse(
        case_id=case.id,
        case_title=case.description or f"Case {case.id}",
        domain=case.domain.value,
        status=case.status.value,
        parties=parties,
        facts=facts,
        evidence=evidence,
        witnesses=witnesses,
        legal_framework=legal_framework,
        arguments=arguments,
        current_verdict=current_verdict,
        created_at=case.created_at,
        last_updated=case.updated_at or case.created_at,
    )
