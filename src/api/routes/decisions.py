from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from src.api.deps import DBSession, require_role
from src.api.schemas.common import ErrorResponse, ValidationErrorResponse
from src.api.schemas.decisions import DecisionAction, DecisionRequest, DecisionResponse
from src.models.audit import AuditLog
from src.models.case import Case, CaseStatus
from src.models.user import User, UserRole

router = APIRouter()


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #


@router.post(
    "/{case_id}/decision",
    response_model=DecisionResponse,
    operation_id="record_decision",
    summary="Record a judge decision",
    description="Record a judge's decision on a case verdict (accept, modify, or reject). "
    "Case must be in `ready_for_review` status. Transitions the case to "
    "`decided` (accept/modify) or `rejected`.",
    responses={
        400: {"model": ErrorResponse, "description": "Case not in ready_for_review status"},
        403: {"model": ErrorResponse, "description": "Insufficient permissions (judge only)"},
        404: {"model": ErrorResponse, "description": "Case not found"},
        422: {"model": ValidationErrorResponse, "description": "Validation error"},
    },
)
async def record_decision(
    case_id: UUID,
    body: DecisionRequest,
    db: DBSession,
    current_user: User = require_role(UserRole.judge),
) -> dict:
    result = await db.execute(select(Case).where(Case.id == case_id).with_for_update())
    case = result.scalar_one_or_none()

    if not case:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found")

    if case.status != CaseStatus.ready_for_review:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Case is in '{case.status.value}' status, must be 'ready_for_review'",
        )

    accepted = body.action in (DecisionAction.accept, DecisionAction.modify)
    new_status = CaseStatus.decided if accepted else CaseStatus.rejected
    case.status = new_status

    audit_entry = AuditLog(
        case_id=case_id,
        agent_name="judge",
        action=f"decision_{body.action.value}",
        input_payload={
            "action": body.action.value,
            "notes": body.notes,
            "final_order": body.final_order,
            "judge_id": str(current_user.id),
        },
    )
    db.add(audit_entry)
    await db.flush()

    return {
        "case_id": case_id,
        "action": body.action,
        "status": new_status,
        "message": f"Case {body.action.value}ed by judge",
    }
