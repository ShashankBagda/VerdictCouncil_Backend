from datetime import datetime
from io import BytesIO
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, File, HTTPException, Query, Response, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload
from sse_starlette.sse import EventSourceResponse

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
from src.services.case_report_data import build_case_report_data
from src.services.hearing_pack import assemble_pack
from src.services.pdf_export import render_case_report_pdf
from src.services.pipeline_events import subscribe as subscribe_pipeline_events
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
    summary="List cases with pagination, search, and filters",
    description="List cases with optional status/domain filters, full-text search on "
    "description (`q`), and created_at date range (`date_from`/`date_to`). "
    "Clerks and judges see only their own cases; admins see all.",
)
async def list_cases(
    db: DBSession,
    current_user: CurrentUser,
    status_filter: CaseStatus | None = Query(None, alias="status"),
    domain: CaseDomain | None = None,
    q: str | None = Query(
        None,
        min_length=1,
        max_length=200,
        description="Full-text search term applied to case description",
    ),
    date_from: datetime | None = Query(
        None, description="Lower bound (inclusive) on case created_at"
    ),
    date_to: datetime | None = Query(
        None, description="Upper bound (inclusive) on case created_at"
    ),
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
    if q:
        if len(q) < 3:
            # tsquery is wasteful for very short input; fall back to ILIKE
            query = query.where(Case.description.ilike(f"%{q}%"))
        else:
            # Postgres tsquery — use plainto_tsquery so user input does not need
            # to be escaped for tsquery operators.
            query = query.where(
                func.to_tsvector("simple", func.coalesce(Case.description, "")).op("@@")(
                    func.plainto_tsquery("simple", q)
                )
            )
    if date_from:
        query = query.where(Case.created_at >= date_from)
    if date_to:
        query = query.where(Case.created_at <= date_to)

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
# Pipeline Trigger (US-??? — mesh path)
# --------------------------------------------------------------------------- #


async def _run_mesh_pipeline(case_id: UUID) -> None:
    """Background task: run the mesh pipeline and persist results.

    Loaded inline to avoid circular imports (mesh_runner_factory pulls
    in Solace + Pydantic settings that other route handlers don't need
    at import time). Owns its own DB session so it survives the
    request-scoped session closing before the pipeline finishes.
    """
    import logging

    from src.db.persist_case_results import persist_case_results
    from src.pipeline.mesh_runner_factory import get_mesh_runner
    from src.services.database import async_session
    from src.shared.case_state import CaseState, CaseStatusEnum

    logger = logging.getLogger(__name__)

    async with async_session() as db:
        case_row = await db.get(Case, case_id)
        if case_row is None:
            logger.error("Mesh pipeline triggered for missing case_id=%s", case_id)
            return

        # Build the starting CaseState from the intake case + its documents.
        docs_result = await db.execute(
            select(Document).where(Document.case_id == case_id)
        )
        documents = list(docs_result.scalars().all())
        raw_documents = [
            {
                "doc_id": str(doc.id),
                "filename": doc.filename,
                "openai_file_id": doc.openai_file_id,
                "file_type": doc.file_type,
            }
            for doc in documents
        ]
        starting_state = CaseState(
            case_id=str(case_id),
            case_metadata={"description": case_row.description or ""},
            raw_documents=raw_documents,
        )

    try:
        runner = await get_mesh_runner()
        final_state = await runner.run(starting_state)
    except Exception:
        logger.exception("Mesh pipeline run failed for case_id=%s", case_id)
        async with async_session() as db:
            case_row = await db.get(Case, case_id)
            if case_row is not None:
                case_row.status = CaseStatus.failed_retryable
                await db.commit()
        return

    if final_state.status == CaseStatusEnum.pending:
        final_state.status = CaseStatusEnum.ready_for_review

    async with async_session() as db:
        await persist_case_results(db, case_id, final_state)


@router.post(
    "/{case_id}/process",
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="trigger_case_processing",
    summary="Trigger the mesh pipeline for a case",
    description=(
        "Kicks off the distributed 9-agent pipeline for a case. Returns "
        "`202 Accepted` immediately; progress is observable via "
        "`GET /api/v1/cases/{case_id}/status/stream` and final results "
        "land in the relational tables (`Evidence`, `Fact`, `Verdict`, etc.) "
        "once the pipeline completes."
    ),
    responses={
        400: {"model": ErrorResponse, "description": "Case not eligible for processing"},
        403: {"model": ErrorResponse, "description": "Not authorized to process this case"},
        404: {"model": ErrorResponse, "description": "Case not found"},
    },
)
async def trigger_case_processing(
    case_id: UUID,
    background_tasks: BackgroundTasks,
    db: DBSession,
    current_user: CurrentUser,
) -> dict:
    case = (await db.execute(select(Case).where(Case.id == case_id))).scalar_one_or_none()
    if case is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found")
    if current_user.role == UserRole.clerk and case.created_by != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to process this case",
        )
    if case.status not in (CaseStatus.pending, CaseStatus.failed_retryable):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Case must be in 'pending' or 'failed_retryable' status to trigger "
                f"processing. Current status: '{case.status.value}'"
            ),
        )

    case.status = CaseStatus.processing
    await db.flush()

    background_tasks.add_task(_run_mesh_pipeline, case_id)

    return {
        "case_id": str(case_id),
        "status": CaseStatus.processing.value,
        "message": (
            "Mesh pipeline started. Subscribe to /status/stream for agent progress."
        ),
    }


# --------------------------------------------------------------------------- #
# Pipeline Status SSE Stream (US-002)
# --------------------------------------------------------------------------- #


@router.get(
    "/{case_id}/status/stream",
    operation_id="stream_pipeline_status",
    summary="Server-Sent Events stream of pipeline agent progress",
    description=(
        "Streams `PipelineProgressEvent` JSON objects as SSE messages for the "
        "lifetime of the pipeline run, closing once the governance-verdict agent "
        "reaches a terminal phase. Authentication uses the same cookie as the "
        "rest of the API."
    ),
    responses={
        403: {"model": ErrorResponse, "description": "Not authorized to view this case"},
        404: {"model": ErrorResponse, "description": "Case not found"},
    },
)
async def stream_pipeline_status(
    case_id: UUID,
    db: DBSession,
    current_user: CurrentUser,
) -> EventSourceResponse:
    case = (await db.execute(select(Case).where(Case.id == case_id))).scalar_one_or_none()
    if case is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found")
    if current_user.role == UserRole.clerk and case.created_by != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to view this case",
        )

    async def event_source():
        async for event_json in subscribe_pipeline_events(str(case_id)):
            yield {"data": event_json}

    return EventSourceResponse(event_source())


# --------------------------------------------------------------------------- #
# Hearing Pack Export (US-020)
# --------------------------------------------------------------------------- #


@router.get(
    "/{case_id}/hearing-pack",
    operation_id="get_hearing_pack",
    summary="Download a hearing-prep zip pack for a case",
    description=(
        "Returns a zip archive containing a manifest, case summary, evidence, "
        "facts, arguments, and verdict for the given case. Suitable for offline "
        "preparation before a hearing."
    ),
    responses={
        403: {"model": ErrorResponse, "description": "Not authorized to view this case"},
        404: {"model": ErrorResponse, "description": "Case not found"},
    },
)
async def get_hearing_pack(
    case_id: UUID,
    db: DBSession,
    current_user: CurrentUser,
) -> StreamingResponse:
    # Quick existence + ownership check before loading every relation
    case_row = (await db.execute(select(Case).where(Case.id == case_id))).scalar_one_or_none()
    if case_row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found")
    if current_user.role == UserRole.clerk and case_row.created_by != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to view this case",
        )

    data = await build_case_report_data(db, case_id)
    if data is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found")

    pack_bytes = assemble_pack(data)
    return StreamingResponse(
        BytesIO(pack_bytes),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="case-{case_id}-hearing-pack.zip"',
        },
    )


# --------------------------------------------------------------------------- #
# Case Report PDF Export (US-027)
# --------------------------------------------------------------------------- #


@router.get(
    "/{case_id}/report.pdf",
    operation_id="get_case_report_pdf",
    summary="Download case report as PDF",
    description=(
        "Renders the case summary, parties, evidence, facts, arguments, and "
        "verdict into a single PDF document. Suitable for archival or printout."
    ),
    responses={
        403: {"model": ErrorResponse, "description": "Not authorized to view this case"},
        404: {"model": ErrorResponse, "description": "Case not found"},
    },
)
async def get_case_report_pdf(
    case_id: UUID,
    db: DBSession,
    current_user: CurrentUser,
) -> Response:
    case_row = (await db.execute(select(Case).where(Case.id == case_id))).scalar_one_or_none()
    if case_row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found")
    if current_user.role == UserRole.clerk and case_row.created_by != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to view this case",
        )

    data = await build_case_report_data(db, case_id)
    if data is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found")

    pdf_bytes = render_case_report_pdf(data)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="case-{case_id}-report.pdf"',
        },
    )


# --------------------------------------------------------------------------- #
# Document Upload
# --------------------------------------------------------------------------- #


_MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # 50 MB
_ALLOWED_CONTENT_TYPES = {
    "application/pdf",
    "image/png",
    "image/jpeg",
    "text/plain",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


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

    if file.content_type and file.content_type not in _ALLOWED_CONTENT_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"File type '{file.content_type}' not allowed",
        )

    file_bytes = await file.read()
    if len(file_bytes) > _MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"File exceeds {_MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit",
        )

    # Upload to OpenAI Files API for pipeline processing
    from openai import AsyncOpenAI

    from src.shared.config import settings

    client = AsyncOpenAI(api_key=settings.openai_api_key)
    openai_file = await client.files.create(
        file=(file.filename or "document", file_bytes),
        purpose="assistants",
    )

    doc = Document(
        case_id=case_id,
        openai_file_id=openai_file.id,
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
        "openai_file_id": openai_file.id,
        "filename": doc.filename,
        "file_type": doc.file_type,
        "size_bytes": len(file_bytes),
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
