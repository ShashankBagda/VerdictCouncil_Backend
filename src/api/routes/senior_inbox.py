"""Senior judge inbox aggregation endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from src.api.deps import DBSession, require_role
from src.models.case import Case, CaseStatus, ReopenRequest, ReopenRequestStatus, Verdict
from src.models.user import User, UserRole

router = APIRouter()


def _optional_text(value: Any) -> str | None:
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return None


def _serialize_escalation_item(case: Case) -> dict:
    priority = "urgent" if case.complexity and case.complexity.value == "high" else "high"
    reason = "Escalated for senior review."
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
                    reason = value
                    break
            if reason != "Escalated for senior review.":
                break

    history = [
        {
            "action": log.action,
            "reason": (
                (log.input_payload or {}).get("notes") or (log.input_payload or {}).get("reason")
            ),
            "actor": (log.input_payload or {}).get("judge_id") or log.agent_name,
            "created_at": log.created_at,
            "details": log.output_payload,
        }
        for log in sorted(
            case.audit_logs or [],
            key=lambda item: item.created_at.timestamp() if item.created_at else 0.0,
        )
        if "escalat" in log.action.lower()
    ]

    return {
        "id": f"escalation:{case.id}",
        "case_id": str(case.id),
        "item_type": "escalation",
        "originating_judge": str(case.created_by),
        "reason": reason,
        "priority": priority,
        "submitted_at": (case.updated_at or case.created_at).isoformat()
        if (case.updated_at or case.created_at)
        else None,
        "status": "pending",
        "preview": _optional_text(case.description) or _optional_text(case.title) or reason,
        "case_title": _optional_text(case.title) or f"Case {case.id}",
        "domain": case.domain.value,
        "history": history,
    }


def _serialize_reopen_item(request_item: ReopenRequest) -> dict:
    case = request_item.case
    history = [
        {
            "action": "reopen_request_create",
            "reason": request_item.justification,
            "actor": str(request_item.requested_by),
            "created_at": request_item.created_at,
            "details": {"reason": request_item.reason},
        }
    ]
    return {
        "id": f"reopen:{request_item.id}",
        "case_id": str(request_item.case_id),
        "item_type": "reopen",
        "originating_judge": str(request_item.requested_by),
        "reason": request_item.reason,
        "priority": "urgent" if request_item.reason in {"appeal", "clerical_error"} else "medium",
        "submitted_at": request_item.created_at.isoformat() if request_item.created_at else None,
        "status": request_item.status.value,
        "preview": _optional_text(request_item.justification),
        "case_title": (
            (_optional_text(case.title) if case else None) or f"Case {request_item.case_id}"
        ),
        "domain": case.domain.value if case and case.domain else None,
        "history": history,
    }


def _serialize_amendment_item(verdict: Verdict) -> dict:
    case = verdict.case
    return {
        "id": f"amendment:{verdict.id}",
        "case_id": str(verdict.case_id),
        "item_type": "amendment",
        "originating_judge": str(verdict.amended_by) if verdict.amended_by else None,
        "reason": verdict.amendment_reason or "Decision amendment awaiting senior review.",
        "priority": "medium",
        "submitted_at": None,
        "status": "pending",
        "preview": _optional_text(verdict.recommended_outcome),
        "case_title": (_optional_text(case.title) if case else None) or f"Case {verdict.case_id}",
        "domain": case.domain.value if case and case.domain else None,
        "history": [],
    }


@router.get(
    "/",
    operation_id="list_senior_inbox",
    summary="List senior judge inbox items",
    description="Aggregates escalations, decision amendments, and pending reopen requests.",
)
async def list_senior_inbox(
    db: DBSession,
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    current_user: User = require_role(UserRole.senior_judge),
) -> dict:
    escalated_cases = (
        (
            await db.execute(
                select(Case)
                .where(Case.status == CaseStatus.escalated)
                .options(selectinload(Case.audit_logs))
            )
        )
        .scalars()
        .all()
    )

    pending_reopen = (
        (
            await db.execute(
                select(ReopenRequest)
                .where(ReopenRequest.status == ReopenRequestStatus.pending)
                .options(selectinload(ReopenRequest.case))
            )
        )
        .scalars()
        .all()
    )

    amended_verdicts = (
        (
            await db.execute(
                select(Verdict)
                .where(Verdict.amendment_of.is_not(None))
                .options(selectinload(Verdict.case))
            )
        )
        .scalars()
        .all()
    )

    items = [
        *[_serialize_escalation_item(case) for case in escalated_cases],
        *[_serialize_reopen_item(request_item) for request_item in pending_reopen],
        *[_serialize_amendment_item(verdict) for verdict in amended_verdicts],
    ]

    priority_order = {"urgent": 0, "high": 1, "medium": 2, "low": 3}
    items.sort(
        key=lambda item: (
            priority_order.get(item.get("priority", "low"), 4),
            item.get("submitted_at") or "",
        )
    )

    total = len(items)
    start = (page - 1) * per_page
    paginated = items[start : start + per_page]

    counts = {
        "escalation": len(escalated_cases),
        "reopen": len(pending_reopen),
        "amendment": len(amended_verdicts),
    }

    return {
        "items": paginated,
        "total": total,
        "page": page,
        "per_page": per_page,
        "counts": counts,
    }
