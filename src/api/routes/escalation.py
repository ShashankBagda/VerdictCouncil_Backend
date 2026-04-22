"""Escalated case handling endpoints (US-024)."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from src.api.deps import DBSession, require_role
from src.api.schemas.common import ErrorResponse
from src.api.schemas.escalation import (
    EscalatedCaseListResponse,
    EscalationAction,
    EscalationActionRequest,
    EscalationActionResponse,
)
from src.models.audit import AuditLog
from src.models.case import Case, CaseStatus
from src.models.user import User, UserRole

router = APIRouter()

_AGENT_NAME = "judge"


def _enum_value(value: Any) -> str | None:
    if value is None:
        return None
    return value.value if hasattr(value, "value") else str(value)


def _optional_text(value: Any) -> str | None:
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return None


def _extract_escalation_reason(case: Case) -> str:
    for log in sorted(
        case.audit_logs or [],
        key=lambda item: item.created_at.timestamp() if item.created_at else 0.0,
        reverse=True,
    ):
        if "escalat" not in log.action.lower():
            continue
        for payload in (log.output_payload or {}, log.input_payload or {}):
            for key in ("reason", "escalation_reason", "summary", "detail", "message"):
                value = payload.get(key)
                if isinstance(value, str) and value.strip():
                    return value
    route = _enum_value(case.route)
    if route == "escalate_human":
        complexity = _enum_value(case.complexity) or "unspecified"
        return f"Routed for human review because complexity is {complexity}."
    return "Escalated for judicial review."


def _serialize_history(case: Case) -> list[dict]:
    entries: list[dict] = []
    for log in sorted(
        case.audit_logs or [],
        key=lambda item: item.created_at.timestamp() if item.created_at else 0.0,
    ):
        if "escalat" not in log.action.lower():
            continue
        payload = log.input_payload or {}
        entries.append(
            {
                "action": log.action,
                "reason": payload.get("notes") or payload.get("reason"),
                "actor": payload.get("judge_id") or log.agent_name,
                "created_at": log.created_at,
                "details": log.output_payload,
            }
        )
    return entries


def _serialize_escalation_item(case: Case) -> dict:
    reason = _extract_escalation_reason(case)
    submitted_at = case.updated_at or case.created_at
    complexity = _enum_value(case.complexity)
    route = _enum_value(case.route)
    priority = "urgent" if complexity == "high" else "high"
    case_title = _optional_text(getattr(case, "title", None)) or f"Case {case.id}"
    preview = _optional_text(getattr(case, "description", None)) or reason
    return {
        "id": str(case.id),
        "case_id": case.id,
        "item_type": "escalation",
        "case_title": case_title,
        "domain": case.domain,
        "status": case.status,
        "route": route,
        "complexity": complexity,
        "originating_judge": str(case.created_by),
        "reason": reason,
        "priority": priority,
        "submitted_at": submitted_at,
        "preview": preview,
        "history": _serialize_history(case),
    }


@router.get(
    "/",
    response_model=EscalatedCaseListResponse,
    operation_id="list_escalated_cases",
    summary="List escalated cases",
    description=(
        "Returns all cases currently in escalated status with pagination. Requires judge role."
    ),
    responses={
        403: {"model": ErrorResponse, "description": "Insufficient permissions"},
    },
)
async def list_escalated_cases(
    db: DBSession,
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    current_user: User = require_role(UserRole.judge, UserRole.senior_judge),
) -> dict:
    query = (
        select(Case)
        .where(Case.status == CaseStatus.escalated)
        .options(selectinload(Case.audit_logs))
    )

    count_query = select(func.count()).select_from(query.order_by(None).subquery())
    total = (await db.execute(count_query)).scalar_one()

    query = query.order_by(Case.updated_at.desc().nullslast(), Case.created_at.desc())
    query = query.offset((page - 1) * per_page).limit(per_page)
    result = await db.execute(query)
    items = list(result.scalars().all())

    return {
        "items": [_serialize_escalation_item(item) for item in items],
        "total": total,
        "page": page,
        "per_page": per_page,
    }


@router.post(
    "/{case_id}/action",
    response_model=EscalationActionResponse,
    operation_id="take_escalation_action",
    summary="Take action on an escalated case",
    description=(
        "Available actions: `add_notes` (stays escalated), `return_to_pipeline` (-> processing), "
        "`close` (-> closed). Requires judge role."
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
    current_user: User = require_role(UserRole.judge, UserRole.senior_judge),
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

    previous_status = case.status

    status_map = {
        EscalationAction.add_notes: CaseStatus.escalated,
        EscalationAction.return_to_pipeline: CaseStatus.processing,
        EscalationAction.close: CaseStatus.closed,
    }
    case.status = status_map[body.action]

    input_payload: dict = {"action": body.action.value, "judge_id": str(current_user.id)}
    if body.notes:
        input_payload["notes"] = body.notes

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
        EscalationAction.close: "Case has been closed.",
    }

    return EscalationActionResponse(
        case_id=case_id,
        action=body.action,
        previous_status=previous_status,
        new_status=case.status,
        message=message_map[body.action],
    )
