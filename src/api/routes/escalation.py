"""Escalated case handling endpoints (US-024)."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, select

from src.api.deps import DBSession, require_role
from src.api.schemas.common import ErrorResponse
from src.api.schemas.escalation import (
    EscalatedCaseListResponse,
    EscalationAction,
    EscalationActionRequest,
    EscalationActionResponse,
)
from src.models.audit import AuditLog
from src.models.case import Case, CaseStatus, RecommendationType, Verdict
from src.models.user import User, UserRole

router = APIRouter()

_AGENT_NAME = "judge"


@router.get(
    "/",
    response_model=EscalatedCaseListResponse,
    operation_id="list_escalated_cases",
    summary="List escalated cases",
    description="Returns all cases currently in escalated status with pagination. "
    "Requires judge role.",
    responses={
        403: {"model": ErrorResponse, "description": "Insufficient permissions"},
    },
)
async def list_escalated_cases(
    db: DBSession,
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    current_user: User = require_role(UserRole.judge),
) -> dict:
    query = select(Case).where(Case.status == CaseStatus.escalated)

    count_query = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_query)).scalar_one()

    query = query.offset((page - 1) * per_page).limit(per_page)
    result = await db.execute(query)
    items = list(result.scalars().all())

    return {"items": items, "total": total, "page": page, "per_page": per_page}


@router.post(
    "/{case_id}/action",
    response_model=EscalationActionResponse,
    operation_id="take_escalation_action",
    summary="Take action on an escalated case",
    description=(
        "Available actions: `add_notes` (stays escalated), `return_to_pipeline` (→ processing), "
        "`manual_decision` (→ decided, requires `final_order`), `reject` (→ rejected). "
        "Requires judge role."
    ),
    responses={
        400: {"model": ErrorResponse, "description": "Case is not in escalated status"},
        403: {"model": ErrorResponse, "description": "Insufficient permissions"},
        404: {"model": ErrorResponse, "description": "Case not found"},
        422: {"model": ErrorResponse, "description": "Validation error"},
    },
)
async def take_escalation_action(
    case_id: UUID,
    body: EscalationActionRequest,
    db: DBSession,
    current_user: User = require_role(UserRole.judge),
) -> EscalationActionResponse:
    result = await db.execute(select(Case).where(Case.id == case_id).with_for_update())
    case = result.scalar_one_or_none()
    if not case:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found")

    if case.status != CaseStatus.escalated:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Case is not in escalated status (current: {case.status.value})",
        )

    if body.action == EscalationAction.manual_decision and not body.final_order:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="final_order is required for manual_decision action",
        )

    previous_status = case.status

    status_map = {
        EscalationAction.add_notes: CaseStatus.escalated,
        EscalationAction.return_to_pipeline: CaseStatus.processing,
        EscalationAction.manual_decision: CaseStatus.decided,
        EscalationAction.reject: CaseStatus.rejected,
    }
    case.status = status_map[body.action]

    input_payload: dict = {"action": body.action.value}
    if body.notes:
        input_payload["notes"] = body.notes
    if body.final_order:
        input_payload["final_order"] = body.final_order

    if body.action == EscalationAction.manual_decision and body.final_order:
        verdict = Verdict(
            case_id=case_id,
            recommendation_type=RecommendationType.manual_decision,
            recommended_outcome=body.final_order,
        )
        db.add(verdict)

    audit = AuditLog(
        case_id=case_id,
        agent_name=_AGENT_NAME,
        action=f"escalation_{body.action.value}",
        input_payload=input_payload,
        output_payload={"previous_status": previous_status.value, "new_status": case.status.value},
    )
    db.add(audit)
    await db.flush()

    message_map = {
        EscalationAction.add_notes: "Notes recorded. Case remains escalated.",
        EscalationAction.return_to_pipeline: (
            "Case status set to processing. Pipeline will pick it up on next poll."
        ),
        EscalationAction.manual_decision: "Manual decision recorded. Case marked as decided.",
        EscalationAction.reject: "Case has been rejected.",
    }

    return EscalationActionResponse(
        case_id=case_id,
        action=body.action,
        previous_status=previous_status,
        new_status=case.status,
        message=message_map[body.action],
    )
