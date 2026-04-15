from uuid import UUID

from fastapi import APIRouter, File, HTTPException, Query, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from src.api.deps import CurrentUser, DBSession, require_role
from src.api.schemas.cases import (
    CaseCreateRequest,
    CaseDetailResponse,
    CaseListResponse,
    CaseResponse,
)
from src.api.schemas.common import ErrorResponse, ValidationErrorResponse
from src.models.case import (
    Case,
    CaseDomain,
    CaseStatus,
    Document,
)
from src.models.user import User, UserRole
from src.shared.sanitization import sanitize_user_input

router = APIRouter()


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #


@router.post(
    "/",
    response_model=CaseResponse,
    status_code=status.HTTP_201_CREATED,
    operation_id="create_case",
    summary="Create a new case",
    description="Create a new judicial case in the specified domain. Requires clerk or judge role.",
    responses={
        403: {"model": ErrorResponse, "description": "Insufficient permissions"},
        422: {"model": ValidationErrorResponse, "description": "Validation error"},
    },
)
async def create_case(
    body: CaseCreateRequest,
    db: DBSession,
    current_user: User = require_role(UserRole.clerk, UserRole.judge),
) -> Case:
    description = sanitize_user_input(body.description) if body.description else None
    case = Case(
        domain=body.domain,
        description=description,
        created_by=current_user.id,
    )
    db.add(case)
    await db.flush()
    await db.refresh(case)
    return case


@router.get(
    "/",
    response_model=CaseListResponse,
    operation_id="list_cases",
    summary="List cases with pagination",
    description="List cases with optional status and domain filters. "
    "Clerks and judges see only their own cases; admins see all.",
)
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


@router.get(
    "/{case_id}",
    response_model=CaseDetailResponse,
    operation_id="get_case",
    summary="Get full case details",
    description="Retrieve a case with all related entities: parties, documents, "
    "evidence, facts, witnesses, legal rules, precedents, arguments, "
    "deliberations, verdicts, and audit logs.",
    responses={
        403: {"model": ErrorResponse, "description": "Not authorized to view this case"},
        404: {"model": ErrorResponse, "description": "Case not found"},
    },
)
async def get_case(
    case_id: UUID,
    db: DBSession,
    current_user: CurrentUser,
) -> Case:
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

    return case


# --------------------------------------------------------------------------- #
# Document Upload
# --------------------------------------------------------------------------- #


@router.post(
    "/{case_id}/documents",
    status_code=status.HTTP_201_CREATED,
    operation_id="upload_case_document",
    summary="Upload a document to a case",
)
async def upload_case_document(
    case_id: UUID,
    db: DBSession,
    current_user: CurrentUser,
    file: UploadFile = File(...),
) -> dict:
    case = (await db.execute(select(Case).where(Case.id == case_id))).scalar_one_or_none()
    if not case:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found")
    if case.created_by != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not your case")

    doc = Document(
        case_id=case_id,
        filename=file.filename or "untitled",
        file_type=file.content_type,
        uploaded_by=current_user.id,
    )
    db.add(doc)
    await db.flush()
    await db.refresh(doc)

    return {
        "id": str(doc.id),
        "case_id": str(case_id),
        "filename": doc.filename,
        "file_type": doc.file_type,
        "uploaded_at": doc.uploaded_at.isoformat(),
    }


@router.get(
    "/{case_id}/documents",
    operation_id="list_case_documents",
    summary="List documents for a case",
)
async def list_case_documents(
    case_id: UUID,
    db: DBSession,
    current_user: CurrentUser,
) -> dict:
    case = (await db.execute(select(Case).where(Case.id == case_id))).scalar_one_or_none()
    if not case:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found")
    if case.created_by != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not your case")

    result = await db.execute(select(Document).where(Document.case_id == case_id))
    docs = result.scalars().all()

    return {
        "documents": [
            {
                "id": str(d.id),
                "filename": d.filename,
                "file_type": d.file_type,
                "uploaded_at": d.uploaded_at.isoformat(),
            }
            for d in docs
        ]
    }
