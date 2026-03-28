import enum
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select

from src.api.deps import DBSession, require_role
from src.models.audit import AuditLog
from src.models.case import Case, CaseStatus
from src.models.user import User, UserRole

router = APIRouter()


# --------------------------------------------------------------------------- #
# Schemas
# --------------------------------------------------------------------------- #


class DecisionAction(str, enum.Enum):
    accept = "accept"
    modify = "modify"
    reject = "reject"


class DecisionRequest(BaseModel):
    action: DecisionAction
    notes: str | None = None
    final_order: str | None = None


class DecisionResponse(BaseModel):
    case_id: UUID
    action: DecisionAction
    status: CaseStatus
    message: str


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #


@router.post("/{case_id}/decision", response_model=DecisionResponse)
async def record_decision(
    case_id: UUID,
    body: DecisionRequest,
    db: DBSession,
    current_user: User = require_role(UserRole.judge),
) -> dict:
    result = await db.execute(select(Case).where(Case.id == case_id))
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
