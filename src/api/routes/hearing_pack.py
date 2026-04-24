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


def _listify(value):
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        for key in ("items", "questions", "suggested_questions"):
            nested = value.get(key)
            if isinstance(nested, list):
                return nested
    return []


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
    current_user: User = require_role(UserRole.judge),
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
    disputed_issues = [
        {
            "id": str(f.id),
            "description": f.description,
            "confidence": f.confidence.value if f.confidence else None,
            "status": f.status.value if f.status else None,
            "dispute_reason": (f.corroboration or {}).get("dispute_reason")
            if isinstance(f.corroboration, dict)
            else None,
        }
        for f in case.facts
        if f.status and f.status.value == "disputed"
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
    evidence_gaps = [
        {
            "id": f"weak-{e.id}",
            "type": "weak_evidence",
            "description": f"Weak {e.evidence_type.value} evidence item",
            "status": e.strength.value if e.strength else None,
            "details": e.admissibility_flags or e.linked_claims,
        }
        for e in case.evidence
        if e.strength and e.strength.value == "weak"
    ] + [
        {
            "id": f"uncorroborated-{f.id}",
            "type": "uncorroborated_fact",
            "description": f.description,
            "status": f.status.value if f.status else None,
            "details": f.corroboration,
        }
        for f in case.facts
        if not f.corroboration
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
        "prosecution": [
            {
                "id": str(a.id),
                "legal_basis": a.legal_basis,
                "weaknesses": a.weaknesses,
            }
            for a in case.arguments
            if a.side.value == "prosecution"
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
        "defense": [
            {
                "id": str(a.id),
                "legal_basis": a.legal_basis,
                "weaknesses": a.weaknesses,
            }
            for a in case.arguments
            if a.side.value == "defense"
        ],
    }
    suggested_questions = []
    weak_points = []
    for argument in case.arguments:
        if argument.weaknesses:
            weak_points.append(
                {
                    "side": argument.side.value,
                    "issue": argument.legal_basis,
                    "weakness": argument.weaknesses,
                }
            )
        for index, question in enumerate(_listify(argument.suggested_questions)):
            if isinstance(question, dict):
                suggested_questions.append(
                    {
                        "id": question.get("id") or f"{argument.id}-{index}",
                        "side": argument.side.value,
                        "text": question.get("text") or question.get("question"),
                        "type": question.get("type"),
                        "linked_issue": question.get("linked_issue") or argument.legal_basis,
                    }
                )
            elif isinstance(question, str):
                suggested_questions.append(
                    {
                        "id": f"{argument.id}-{index}",
                        "side": argument.side.value,
                        "text": question,
                        "type": None,
                        "linked_issue": argument.legal_basis,
                    }
                )

    current_verdict = case.judicial_decision

    return HearingPackResponse(
        case_id=case.id,
        case_title=case.description or f"Case {case.id}",
        domain=case.domain.value,
        status=case.status.value,
        case_summary=case.description,
        parties=parties,
        facts=facts,
        disputed_issues=disputed_issues,
        evidence=evidence,
        evidence_gaps=evidence_gaps,
        witnesses=witnesses,
        legal_framework=legal_framework,
        arguments=arguments,
        suggested_questions=suggested_questions,
        weak_points=weak_points,
        current_verdict=current_verdict,
        created_at=case.created_at,
        last_updated=case.updated_at or case.created_at,
    )
