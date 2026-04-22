"""Case reopen request workflow endpoints."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from src.api.deps import DBSession, require_role
from src.api.schemas.common import ErrorResponse
from src.api.schemas.reopen_requests import (
    ReopenRequestCreateRequest,
    ReopenRequestListResponse,
    ReopenRequestResponse,
    ReopenRequestReviewRequest,
)
from src.models.audit import AuditLog
from src.models.case import Case, CaseStatus, ReopenRequest, ReopenRequestStatus
from src.models.user import User, UserRole

router = APIRouter()


@router.post(
    "/{case_id}/reopen-request",
    response_model=ReopenRequestResponse,
    operation_id="create_reopen_request",
    summary="Create reopen request",
    responses={
        400: {"model": ErrorResponse, "description": "Case not eligible"},
        404: {"model": ErrorResponse, "description": "Case not found"},
    },
)
async def create_reopen_request(
    case_id: UUID,
    body: ReopenRequestCreateRequest,
    db: DBSession,
    current_user: User = require_role(UserRole.judge, UserRole.senior_judge),
) -> ReopenRequest:
    case = (await db.execute(select(Case).where(Case.id == case_id))).scalar_one_or_none()
    if not case:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found")

    if case.status not in (CaseStatus.decided, CaseStatus.rejected, CaseStatus.closed):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only decided/rejected/closed cases can be reopened",
        )

    request_item = ReopenRequest(
        case_id=case_id,
        requested_by=current_user.id,
        reason=body.reason,
        justification=body.justification,
        status=ReopenRequestStatus.pending,
    )
    db.add(request_item)
    db.add(
        AuditLog(
            case_id=case_id,
            agent_name="judge",
            action="reopen_request_create",
            input_payload={"reason": body.reason, "requested_by": str(current_user.id)},
        )
    )
    await db.flush()
    await db.refresh(request_item)
    return request_item


@router.get(
    "/{case_id}/reopen-requests",
    response_model=ReopenRequestListResponse,
    operation_id="list_reopen_requests",
    summary="List reopen requests",
)
async def list_reopen_requests(
    case_id: UUID,
    db: DBSession,
    current_user: User = require_role(UserRole.judge, UserRole.senior_judge),
) -> ReopenRequestListResponse:
    result = await db.execute(select(ReopenRequest).where(ReopenRequest.case_id == case_id))
    items = list(result.scalars().all())
    return ReopenRequestListResponse(items=items, total=len(items))


@router.patch(
    "/{case_id}/reopen-requests/{request_id}/review",
    response_model=ReopenRequestResponse,
    operation_id="review_reopen_request",
    summary="Review reopen request",
    responses={
        403: {"model": ErrorResponse, "description": "Two-person rule violation"},
        404: {"model": ErrorResponse, "description": "Request not found"},
    },
)
async def review_reopen_request(
    case_id: UUID,
    request_id: UUID,
    body: ReopenRequestReviewRequest,
    db: DBSession,
    current_user: User = require_role(UserRole.senior_judge),
) -> ReopenRequest:
    result = await db.execute(
        select(ReopenRequest).where(
            ReopenRequest.id == request_id, ReopenRequest.case_id == case_id
        )
    )
    request_item = result.scalar_one_or_none()
    if not request_item:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Reopen request not found"
        )

    if request_item.requested_by == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Two-person rule: cannot review your own reopen request",
        )

    request_item.status = (
        ReopenRequestStatus.approved if body.approve else ReopenRequestStatus.rejected
    )
    request_item.reviewed_by = current_user.id
    request_item.reviewed_at = datetime.now(UTC)
    request_item.review_notes = body.review_notes

    if request_item.status == ReopenRequestStatus.approved:
        case = (await db.execute(select(Case).where(Case.id == case_id))).scalar_one_or_none()
        if case:
            case.status = CaseStatus.processing
            from src.models.pipeline_job import PipelineJobType
            from src.workers.outbox import enqueue_outbox_job

            await enqueue_outbox_job(
                db,
                case_id=case_id,
                job_type=PipelineJobType.case_pipeline,
                payload={"resume_from_stage": "evidence-analysis", "resume_reason": "reopen_approved"},
            )

    db.add(
        AuditLog(
            case_id=case_id,
            agent_name="judge",
            action="reopen_request_review",
            input_payload={"request_id": str(request_id), "approve": body.approve},
            output_payload={"status": request_item.status.value},
        )
    )
    await db.flush()
    await db.refresh(request_item)
    return request_item
