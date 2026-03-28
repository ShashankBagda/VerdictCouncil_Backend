from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from src.api.deps import CurrentUser, DBSession, require_role
from src.models.case import (
    Case,
    CaseDomain,
    CaseStatus,
)
from src.models.user import User, UserRole

router = APIRouter()


# --------------------------------------------------------------------------- #
# Schemas
# --------------------------------------------------------------------------- #


class CaseCreateRequest(BaseModel):
    domain: CaseDomain
    description: str | None = None


class CaseResponse(BaseModel):
    id: UUID
    domain: CaseDomain
    status: CaseStatus
    jurisdiction_valid: bool | None = None
    complexity: str | None = None
    route: str | None = None
    created_by: UUID

    model_config = {"from_attributes": True}


class CaseListResponse(BaseModel):
    items: list[CaseResponse]
    total: int
    page: int
    per_page: int


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #


@router.post(
    "/",
    response_model=CaseResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_case(
    body: CaseCreateRequest,
    db: DBSession,
    current_user: User = require_role(UserRole.clerk, UserRole.judge),
) -> Case:
    case = Case(
        domain=body.domain,
        created_by=current_user.id,
    )
    db.add(case)
    await db.flush()
    await db.refresh(case)
    return case


@router.get("/", response_model=CaseListResponse)
async def list_cases(
    db: DBSession,
    current_user: CurrentUser,
    status_filter: CaseStatus | None = Query(None, alias="status"),
    domain: CaseDomain | None = None,
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
) -> dict:
    query = select(Case)

    # Role-based filtering
    if current_user.role == UserRole.clerk or current_user.role == UserRole.judge:
        query = query.where(Case.created_by == current_user.id)
    # Admin sees all

    if status_filter:
        query = query.where(Case.status == status_filter)
    if domain:
        query = query.where(Case.domain == domain)

    # Count
    count_query = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_query)).scalar_one()

    # Paginate
    query = query.offset((page - 1) * per_page).limit(per_page)
    result = await db.execute(query)
    items = list(result.scalars().all())

    return {"items": items, "total": total, "page": page, "per_page": per_page}


@router.get("/{case_id}", response_model=None)
async def get_case(
    case_id: UUID,
    db: DBSession,
    current_user: CurrentUser,
) -> dict:
    result = await db.execute(
        select(Case)
        .where(Case.id == case_id)
        .options(
            selectinload(Case.parties),
            selectinload(Case.documents),
            selectinload(Case.evidence),
            selectinload(Case.facts),
            selectinload(Case.witnesses),
            selectinload(Case.legal_rules),
            selectinload(Case.precedents),
            selectinload(Case.arguments),
            selectinload(Case.deliberations),
            selectinload(Case.verdicts),
            selectinload(Case.audit_logs),
        )
    )
    case = result.scalar_one_or_none()

    if not case:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found")

    # Role-based access check
    if current_user.role == UserRole.clerk and case.created_by != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to view this case",
        )

    return {
        "id": case.id,
        "domain": case.domain,
        "status": case.status,
        "jurisdiction_valid": case.jurisdiction_valid,
        "complexity": case.complexity,
        "route": case.route,
        "created_by": case.created_by,
        "parties": [
            {"id": p.id, "name": p.name, "role": p.role, "contact_info": p.contact_info}
            for p in case.parties
        ],
        "documents": [
            {
                "id": d.id,
                "filename": d.filename,
                "file_type": d.file_type,
                "uploaded_at": d.uploaded_at.isoformat() if d.uploaded_at else None,
            }
            for d in case.documents
        ],
        "evidence": [
            {
                "id": e.id,
                "evidence_type": e.evidence_type,
                "strength": e.strength,
                "admissibility_flags": e.admissibility_flags,
            }
            for e in case.evidence
        ],
        "facts": [
            {
                "id": f.id,
                "description": f.description,
                "event_date": f.event_date.isoformat() if f.event_date else None,
                "confidence": f.confidence,
                "status": f.status,
            }
            for f in case.facts
        ],
        "witnesses": [
            {
                "id": w.id,
                "name": w.name,
                "role": w.role,
                "credibility_score": w.credibility_score,
            }
            for w in case.witnesses
        ],
        "legal_rules": [
            {
                "id": r.id,
                "statute_name": r.statute_name,
                "section": r.section,
                "relevance_score": r.relevance_score,
            }
            for r in case.legal_rules
        ],
        "precedents": [
            {
                "id": p.id,
                "citation": p.citation,
                "court": p.court,
                "outcome": p.outcome,
                "similarity_score": p.similarity_score,
            }
            for p in case.precedents
        ],
        "arguments": [
            {
                "id": a.id,
                "side": a.side,
                "legal_basis": a.legal_basis,
                "weaknesses": a.weaknesses,
            }
            for a in case.arguments
        ],
        "deliberations": [
            {
                "id": d.id,
                "preliminary_conclusion": d.preliminary_conclusion,
                "confidence_score": d.confidence_score,
            }
            for d in case.deliberations
        ],
        "verdicts": [
            {
                "id": v.id,
                "recommendation_type": v.recommendation_type,
                "recommended_outcome": v.recommended_outcome,
                "confidence_score": v.confidence_score,
            }
            for v in case.verdicts
        ],
        "audit_logs": [
            {
                "id": a.id,
                "agent_name": a.agent_name,
                "action": a.action,
                "created_at": a.created_at.isoformat() if a.created_at else None,
            }
            for a in case.audit_logs
        ],
    }
