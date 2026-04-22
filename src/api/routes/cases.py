import logging
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from src.api.deps import CurrentUser, DBSession, require_role
from src.api.schemas.cases import (
    CaseCreateRequest,
    CaseDetailResponse,
    CaseListResponse,
    CaseResponse,
)
from src.api.schemas.common import ErrorResponse, MessageResponse, ValidationErrorResponse
from src.models.case import (
    Case,
    CaseDomain,
    CaseStatus,
)
from src.models.user import User, UserRole
from src.services.case_report_data import build_case_report_data
from src.services.hearing_pack import assemble_pack
from src.services.pdf_export import render_case_report_pdf
from src.services.pipeline_events import subscribe as subscribe_pipeline_events

logger = logging.getLogger(__name__)

router = APIRouter()


async def _load_case_for_export(case_id: UUID, db, current_user: User) -> Case:
    """Fetch a case and enforce clerk ownership. Raises 404/403."""
    result = await db.execute(select(Case).where(Case.id == case_id))
    case = result.scalar_one_or_none()
    if not case:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found")
    if current_user.role == UserRole.clerk and case.created_by != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to view this case",
        )
    return case


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
    case = Case(
        domain=body.domain,
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
    "/{case_id}/report.pdf",
    operation_id="export_case_report_pdf",
    summary="Export the case as a PDF report",
    description="Render a case summary PDF covering parties, evidence, facts, arguments, "
    "verdict, and fairness report.",
    responses={
        403: {"model": ErrorResponse, "description": "Not authorized to view this case"},
        404: {"model": ErrorResponse, "description": "Case not found"},
    },
)
async def export_case_report_pdf(
    case_id: UUID,
    db: DBSession,
    current_user: CurrentUser,
) -> Response:
    await _load_case_for_export(case_id, db, current_user)
    data = await build_case_report_data(db, case_id)
    if data is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found")
    pdf_bytes = render_case_report_pdf(data)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "content-disposition": f'attachment; filename="case-{case_id}-report.pdf"',
        },
    )


@router.get(
    "/{case_id}/hearing-pack",
    operation_id="export_hearing_pack",
    summary="Export the hearing pack zip for a case",
    description="Assemble a zip archive of manifest, case summary, evidence, facts, "
    "arguments, and verdict for in-court review.",
    responses={
        403: {"model": ErrorResponse, "description": "Not authorized to view this case"},
        404: {"model": ErrorResponse, "description": "Case not found"},
    },
)
async def export_hearing_pack(
    case_id: UUID,
    db: DBSession,
    current_user: CurrentUser,
) -> Response:
    await _load_case_for_export(case_id, db, current_user)
    data = await build_case_report_data(db, case_id)
    if data is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found")
    zip_bytes = assemble_pack(data)
    return Response(
        content=zip_bytes,
        media_type="application/zip",
        headers={
            "content-disposition": f'attachment; filename="case-{case_id}-hearing-pack.zip"',
        },
    )


@router.get(
    "/{case_id}/status/stream",
    operation_id="stream_pipeline_status",
    summary="Stream pipeline progress events via SSE",
    description="Server-Sent Events stream backed by the Redis progress pub/sub. "
    "Closes when the governance-verdict agent reaches a terminal phase.",
    responses={
        403: {"model": ErrorResponse, "description": "Not authorized to view this case"},
        404: {"model": ErrorResponse, "description": "Case not found"},
    },
)
async def stream_pipeline_status(
    case_id: UUID,
    db: DBSession,
    current_user: CurrentUser,
) -> StreamingResponse:
    await _load_case_for_export(case_id, db, current_user)

    async def event_generator():
        async for payload in subscribe_pipeline_events(case_id):
            yield f"data: {payload}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# --------------------------------------------------------------------------- #
# Pipeline trigger
# --------------------------------------------------------------------------- #


async def _run_case_pipeline(case_id: UUID) -> None:
    """Background task: run the 9-agent pipeline for a case and persist results.

    Imports are deferred to keep FastAPI module-import cheap and to open a fresh
    session for the background task.
    """
    from src.db.persist_case_results import persist_case_results
    from src.models.case import CaseStatus as CaseStatusModel
    from src.services.database import async_session
    from src.shared.case_state import CaseState
    from src.shared.config import settings

    async with async_session() as db:
        case_result = await db.execute(
            select(Case).where(Case.id == case_id).options(selectinload(Case.documents))
        )
        case = case_result.scalar_one_or_none()
        if not case:
            logger.warning("run_case_pipeline: case %s not found", case_id)
            return

        case.status = CaseStatusModel.processing
        await db.commit()

        raw_documents = [
            {
                "document_id": str(d.id),
                "filename": d.filename,
                "file_type": d.file_type,
            }
            for d in case.documents
        ]
        initial_state = CaseState(case_id=str(case.id), raw_documents=raw_documents)

    try:
        if settings.use_mesh_runner:
            from src.pipeline.mesh_runner_factory import get_mesh_runner

            runner = await get_mesh_runner()
            final_state = await runner.run(initial_state, run_id=initial_state.run_id)
        else:
            from src.pipeline.runner import PipelineRunner

            final_state = await PipelineRunner().run(initial_state)
    except Exception:
        logger.exception("Pipeline run failed for case_id=%s", case_id)
        async with async_session() as db:
            await db.execute(select(Case).where(Case.id == case_id))
            db_case = (
                await db.execute(select(Case).where(Case.id == case_id))
            ).scalar_one_or_none()
            if db_case:
                db_case.status = CaseStatusModel.failed
                await db.commit()
        return

    async with async_session() as db:
        await persist_case_results(db, case_id, final_state)


@router.post(
    "/{case_id}/process",
    response_model=MessageResponse,
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="process_case",
    summary="Start the 9-agent pipeline for a case",
    description="Kicks off the multi-agent pipeline asynchronously. Returns 202 "
    "immediately. Subscribe to `GET /cases/{case_id}/status/stream` for "
    "progress updates, or poll `GET /cases/{case_id}/status` for a snapshot.",
    responses={
        400: {
            "model": ErrorResponse,
            "description": "Case has no documents or is in a terminal state",
        },
        403: {"model": ErrorResponse, "description": "Not authorized"},
        404: {"model": ErrorResponse, "description": "Case not found"},
    },
)
async def process_case(
    case_id: UUID,
    background_tasks: BackgroundTasks,
    db: DBSession,
    current_user: CurrentUser,
) -> MessageResponse:
    result = await db.execute(
        select(Case).where(Case.id == case_id).options(selectinload(Case.documents))
    )
    case = result.scalar_one_or_none()
    if not case:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found")

    if current_user.role == UserRole.clerk and case.created_by != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to run this case",
        )

    if case.status in (CaseStatus.processing,):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Pipeline already running for this case",
        )
    if case.status in (CaseStatus.decided, CaseStatus.closed):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Case is in terminal status '{case.status.value}' and cannot be reprocessed",
        )
    if not case.documents:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Case has no uploaded documents",
        )

    background_tasks.add_task(_run_case_pipeline, case_id)

    return MessageResponse(message="Pipeline started")
