from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Response, status
import json
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
)
from src.models.user import User, UserRole
from src.shared.sanitization import sanitize_user_input

router = APIRouter()


def _escape_pdf_text(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _build_simple_pdf(title: str, payload: dict) -> bytes:
    lines = [
        title,
        f"Case ID: {payload.get('id')}",
        f"Domain: {payload.get('domain')}",
        f"Status: {payload.get('status')}",
        "",
        "Description:",
        (payload.get("description") or "")[:1000],
        "",
        "Parties:",
    ]
    for party in payload.get("parties", [])[:20]:
        lines.append(f"- {party.get('name')} ({party.get('role')})")

    lines.append("")
    lines.append("Verdicts:")
    for verdict in payload.get("verdicts", [])[:20]:
        lines.append(
            f"- {verdict.get('recommendation_type')}: {verdict.get('recommended_outcome') or ''}"
        )

    y_start = 770
    y_step = 16
    text_commands: list[str] = ["BT", "/F1 11 Tf"]
    for index, line in enumerate(lines[:40]):
        y = y_start - (index * y_step)
        text_commands.append(f"1 0 0 1 40 {y} Tm ({_escape_pdf_text(str(line))}) Tj")
    text_commands.append("ET")
    content_stream = "\n".join(text_commands).encode("utf-8")

    objects: list[bytes] = []
    objects.append(b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n")
    objects.append(b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n")
    objects.append(
        b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>\nendobj\n"
    )
    objects.append(b"4 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n")
    objects.append(
        f"5 0 obj\n<< /Length {len(content_stream)} >>\nstream\n".encode("utf-8")
        + content_stream
        + b"\nendstream\nendobj\n"
    )

    pdf = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for obj in objects:
        offsets.append(len(pdf))
        pdf.extend(obj)

    xref_offset = len(pdf)
    pdf.extend(f"xref\n0 {len(objects) + 1}\n".encode("utf-8"))
    pdf.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        pdf.extend(f"{offset:010d} 00000 n \n".encode("utf-8"))

    pdf.extend(
        (
            "trailer\n"
            f"<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            "startxref\n"
            f"{xref_offset}\n"
            "%%EOF"
        ).encode("utf-8")
    )
    return bytes(pdf)


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


@router.get(
    "/{case_id}/export",
    operation_id="export_case",
    summary="Export case record",
    description="Export a case in JSON or PDF format.",
    responses={
        404: {"model": ErrorResponse, "description": "Case not found"},
    },
)
async def export_case(
    case_id: UUID,
    db: DBSession,
    current_user: CurrentUser,
    format: str = Query("json"),
) -> Response:
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

    if current_user.role == UserRole.clerk and case.created_by != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to export this case",
        )

    export_payload = {
        "id": str(case.id),
        "domain": case.domain.value,
        "description": case.description,
        "status": case.status.value,
        "created_by": str(case.created_by),
        "parties": [
            {"id": str(p.id), "name": p.name, "role": p.role.value, "contact_info": p.contact_info}
            for p in case.parties
        ],
        "documents": [
            {
                "id": str(d.id),
                "filename": d.filename,
                "file_type": d.file_type,
                "uploaded_by": str(d.uploaded_by) if d.uploaded_by else None,
            }
            for d in case.documents
        ],
        "facts": [
            {
                "id": str(f.id),
                "description": f.description,
                "confidence": f.confidence.value if f.confidence else None,
                "status": f.status.value if f.status else None,
            }
            for f in case.facts
        ],
        "verdicts": [
            {
                "id": str(v.id),
                "recommendation_type": v.recommendation_type.value,
                "recommended_outcome": v.recommended_outcome,
                "confidence_score": v.confidence_score,
                "amendment_of": str(v.amendment_of) if v.amendment_of else None,
                "amendment_reason": v.amendment_reason,
            }
            for v in case.verdicts
        ],
    }

    if format.lower() == "json":
        return Response(
            content=json.dumps(export_payload, default=str, indent=2),
            media_type="application/json",
            headers={"Content-Disposition": f"attachment; filename=case-{case_id}.json"},
        )

    if format.lower() == "pdf":
        pdf_content = _build_simple_pdf(f"Case Export - {case_id}", export_payload)
        return Response(
            content=pdf_content,
            media_type="application/pdf",
            headers={"Content-Disposition": f"attachment; filename=case-{case_id}.pdf"},
        )

    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Unsupported export format. Use format=json or format=pdf.",
    )
