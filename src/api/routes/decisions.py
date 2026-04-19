from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from src.api.deps import DBSession, require_role
from src.api.schemas.common import ErrorResponse, ValidationErrorResponse
from src.api.schemas.decisions import (
    AmendmentRequest,
    DecisionAction,
    DecisionHistoryListResponse,
    DecisionRequest,
    DecisionResponse,
    VerdictHistoryResponse,
)
from src.models.audit import AuditLog
from src.models.case import Case, CaseStatus, RecommendationType, Verdict
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


@router.post(
    "/{case_id}/amend-decision",
    response_model=VerdictHistoryResponse,
    operation_id="amend_decision",
    summary="Amend a case decision",
    description="Create an amended verdict while preserving original verdict history.",
    responses={
        403: {"model": ErrorResponse, "description": "Insufficient permissions"},
        404: {"model": ErrorResponse, "description": "Case or verdict not found"},
        422: {"model": ValidationErrorResponse, "description": "Validation error"},
    },
)
async def amend_decision(
    case_id: UUID,
    body: AmendmentRequest,
    db: DBSession,
    current_user: User = require_role(UserRole.judge, UserRole.senior_judge),
) -> VerdictHistoryResponse:
    case = (await db.execute(select(Case).where(Case.id == case_id))).scalar_one_or_none()
    if not case:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found")

    verdicts = (
        await db.execute(select(Verdict).where(Verdict.case_id == case_id))
    ).scalars().all()
    if not verdicts:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No verdict to amend")

    original = verdicts[-1]
    try:
        recommendation_type = RecommendationType(body.recommendation_type)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Invalid recommendation_type",
        ) from exc

    amended = Verdict(
        case_id=case_id,
        recommendation_type=recommendation_type,
        recommended_outcome=body.recommended_outcome,
        confidence_score=original.confidence_score,
        sentence=original.sentence,
        alternative_outcomes=original.alternative_outcomes,
        fairness_report=original.fairness_report,
        amendment_of=original.id,
        amendment_reason=body.amendment_reason,
        amended_by=current_user.id,
    )
    db.add(amended)
    db.add(
        AuditLog(
            case_id=case_id,
            agent_name="judge",
            action="decision_amend",
            input_payload={
                "original_verdict_id": str(original.id),
                "recommendation_type": body.recommendation_type,
                "amendment_reason": body.amendment_reason,
            },
            output_payload={"amended_verdict_id": str(amended.id)},
        )
    )
    await db.flush()
    await db.refresh(amended)

    return VerdictHistoryResponse(
        verdict_id=amended.id,
        recommendation_type=amended.recommendation_type.value,
        recommended_outcome=amended.recommended_outcome,
        confidence_score=amended.confidence_score,
        amendment_of=amended.amendment_of,
        amendment_reason=amended.amendment_reason,
        amended_by=amended.amended_by,
    )


@router.get(
    "/{case_id}/decision-history",
    response_model=DecisionHistoryListResponse,
    operation_id="list_decision_history",
    summary="List decision history",
    description="Return all verdicts for a case including amendments.",
)
async def list_decision_history(
    case_id: UUID,
    db: DBSession,
    current_user: User = require_role(UserRole.judge, UserRole.senior_judge),
) -> DecisionHistoryListResponse:
    case = (await db.execute(select(Case).where(Case.id == case_id))).scalar_one_or_none()
    if not case:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found")

    verdicts = (
        await db.execute(select(Verdict).where(Verdict.case_id == case_id))
    ).scalars().all()
    items = [
        VerdictHistoryResponse(
            verdict_id=v.id,
            recommendation_type=v.recommendation_type.value,
            recommended_outcome=v.recommended_outcome,
            confidence_score=v.confidence_score,
            amendment_of=v.amendment_of,
            amendment_reason=v.amendment_reason,
            amended_by=v.amended_by,
        )
        for v in verdicts
    ]
    return DecisionHistoryListResponse(case_id=case_id, items=items)
