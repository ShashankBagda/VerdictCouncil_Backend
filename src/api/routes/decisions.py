from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from src.api.deps import DBSession, require_role
from src.api.schemas.common import ErrorResponse, ValidationErrorResponse
from src.api.schemas.decisions import DecisionAction, DecisionRequest, DecisionResponse
from src.api.schemas.workflows import (
    DecisionAmendmentRequest,
    DecisionAmendmentResponse,
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
    recorded_at = datetime.now(UTC)
    audit_entry.created_at = recorded_at

    return {
        "case_id": case_id,
        "action": body.action,
        "status": new_status,
        "decision_type": body.action.value,
        "reason": body.notes,
        "final_order": body.final_order,
        "recorded_at": recorded_at,
        "recorded_by": str(current_user.id),
        "message": f"Case {body.action.value}ed by judge",
    }


def _latest_decision_log(case: Case) -> AuditLog | None:
    decision_logs = [
        log
        for log in (case.audit_logs or [])
        if log.agent_name == "judge"
        and (log.action.startswith("decision_") or log.action == "decision_amendment_apply")
    ]
    if not decision_logs:
        return None
    return max(decision_logs, key=lambda item: item.created_at or datetime.min.replace(tzinfo=UTC))


def _latest_verdict(case: Case) -> Verdict | None:
    verdicts = list(case.verdicts or [])
    if not verdicts:
        return None
    return max(verdicts, key=lambda item: item.created_at or datetime.min.replace(tzinfo=UTC))


def _copy_verdict_payload(base_verdict: Verdict | None, final_order: str) -> dict:
    if base_verdict is None:
        return {
            "recommendation_type": RecommendationType.manual_decision,
            "recommended_outcome": final_order,
            "sentence": None,
            "confidence_score": None,
            "alternative_outcomes": None,
            "fairness_report": None,
            "amendment_of": None,
        }

    return {
        "recommendation_type": base_verdict.recommendation_type,
        "recommended_outcome": final_order,
        "sentence": base_verdict.sentence,
        "confidence_score": base_verdict.confidence_score,
        "alternative_outcomes": base_verdict.alternative_outcomes,
        "fairness_report": base_verdict.fairness_report,
        "amendment_of": base_verdict.id,
    }


@router.post(
    "/{case_id}/decision-amendments",
    response_model=DecisionAmendmentResponse,
    operation_id="submit_decision_amendment",
    summary="Amend or request amendment of a recorded decision",
    responses={
        400: {"model": ErrorResponse, "description": "Case has no recorded decision"},
        403: {"model": ErrorResponse, "description": "Insufficient permissions"},
        404: {"model": ErrorResponse, "description": "Case not found"},
    },
)
async def submit_decision_amendment(
    case_id: UUID,
    body: DecisionAmendmentRequest,
    db: DBSession,
    current_user: User = require_role(UserRole.judge, UserRole.senior_judge),
) -> DecisionAmendmentResponse:
    result = await db.execute(
        select(Case)
        .where(Case.id == case_id)
        .options(selectinload(Case.audit_logs), selectinload(Case.verdicts))
        .with_for_update()
    )
    case = result.scalar_one_or_none()

    if not case:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found")
    if case.status == CaseStatus.closed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Closed cases cannot be amended",
        )

    latest_decision = _latest_decision_log(case)
    if latest_decision is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Case has no recorded decision to amend",
        )

    latest_payload = latest_decision.input_payload or {}
    recording_judge_id = latest_payload.get("judge_id")
    current_user_id = str(current_user.id)
    base_verdict = _latest_verdict(case)

    can_apply_directly = (
        current_user.role == UserRole.senior_judge or recording_judge_id == current_user_id
    )

    audit_payload = {
        "amendment_type": body.amendment_type.value,
        "reason": body.reason,
        "final_order": body.final_order,
        "notes": body.notes,
        "requested_by": current_user_id,
        "recording_judge_id": recording_judge_id,
        "base_verdict_id": str(base_verdict.id) if base_verdict else None,
    }

    if not can_apply_directly:
        request_id = uuid4()
        db.add(
            AuditLog(
                case_id=case_id,
                agent_name="judge",
                action="decision_amendment_request",
                input_payload={**audit_payload, "request_id": str(request_id)},
            )
        )
        await db.flush()
        return DecisionAmendmentResponse(
            case_id=case_id,
            amendment_request_id=request_id,
            amendment_type=body.amendment_type,
            status="pending_senior_review",
            message="Decision amendment routed to the senior judge inbox for review.",
        )

    verdict_payload = _copy_verdict_payload(base_verdict, body.final_order)
    amended_verdict = Verdict(
        case_id=case_id,
        recommendation_type=verdict_payload["recommendation_type"],
        recommended_outcome=verdict_payload["recommended_outcome"],
        sentence=verdict_payload["sentence"],
        confidence_score=verdict_payload["confidence_score"],
        alternative_outcomes=verdict_payload["alternative_outcomes"],
        fairness_report=verdict_payload["fairness_report"],
        amendment_of=verdict_payload["amendment_of"],
        amendment_reason=f"{body.amendment_type.value}: {body.reason}",
        amended_by=current_user.id,
    )
    db.add(amended_verdict)
    db.add(
        AuditLog(
            case_id=case_id,
            agent_name="judge",
            action="decision_amendment_apply",
            input_payload=audit_payload,
            output_payload={"status": "approved"},
        )
    )
    await db.flush()

    return DecisionAmendmentResponse(
        case_id=case_id,
        amendment_type=body.amendment_type,
        status="approved",
        amended_verdict_id=amended_verdict.id,
        message="Decision amendment recorded.",
    )
