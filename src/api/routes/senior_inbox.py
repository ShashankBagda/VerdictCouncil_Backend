"""Senior judge inbox aggregation endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from src.api.deps import DBSession, require_role
from src.models.case import Case, CaseStatus, ReopenRequest, ReopenRequestStatus, Verdict
from src.models.user import User, UserRole

router = APIRouter()


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
        await db.execute(select(Case).where(Case.status == CaseStatus.escalated))
    ).scalars().all()

    pending_reopen = (
        await db.execute(
            select(ReopenRequest).where(ReopenRequest.status == ReopenRequestStatus.pending)
        )
    ).scalars().all()

    amended_verdicts = (
        await db.execute(select(Verdict).where(Verdict.amendment_of.is_not(None)))
    ).scalars().all()

    items: list[dict] = []

    for case in escalated_cases:
        items.append(
            {
                "id": f"escalation:{case.id}",
                "type": "escalation",
                "case_id": str(case.id),
                "priority": "high",
                "submitted_at": (case.updated_at or case.created_at).isoformat()
                if (case.updated_at or case.created_at)
                else None,
                "status": "pending",
            }
        )

    for request_item in pending_reopen:
        items.append(
            {
                "id": f"reopen:{request_item.id}",
                "type": "reopen",
                "case_id": str(request_item.case_id),
                "priority": "medium",
                "submitted_at": request_item.created_at.isoformat() if request_item.created_at else None,
                "status": request_item.status.value,
                "reason": request_item.reason,
            }
        )

    for verdict in amended_verdicts:
        items.append(
            {
                "id": f"amendment:{verdict.id}",
                "type": "amendment",
                "case_id": str(verdict.case_id),
                "priority": "medium",
                "submitted_at": None,
                "status": "pending",
                "amendment_of": str(verdict.amendment_of),
                "amendment_reason": verdict.amendment_reason,
            }
        )

    priority_order = {"high": 0, "medium": 1, "low": 2}
    items.sort(key=lambda x: (priority_order.get(x.get("priority", "low"), 3), x.get("submitted_at") or ""))

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
