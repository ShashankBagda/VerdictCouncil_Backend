"""Sprint 4 4.C4.4 — /cost/summary endpoint.

Aggregates `audit_logs.cost_usd` per case (and optionally a global
window) for the cost dashboard. Updates the Prometheus
``verdict_council_case_cost_usd`` gauge as a side-effect of each
per-case query so dashboards stay fresh.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from src.api.deps import DBSession, require_role
from src.api.middleware.metrics import metrics_store
from src.models.audit import AuditLog
from src.models.case import Case
from src.models.user import User, UserRole

router = APIRouter()


class CostSummaryResponse(BaseModel):
    case_id: UUID | None = Field(
        None,
        description=(
            "When provided, the response sums audit rows for that single case; "
            "otherwise sums across all cases the caller can see."
        ),
    )
    total_usd: Decimal
    audit_row_count: int
    from_ts: datetime | None = None
    to_ts: datetime | None = None


@router.get(
    "/summary",
    response_model=CostSummaryResponse,
    operation_id="cost_summary",
    summary="Aggregate LLM cost across audit_logs",
)
async def cost_summary(
    db: DBSession,
    case_id: Annotated[UUID | None, Query(description="Filter to a single case")] = None,
    from_ts: Annotated[
        datetime | None,
        Query(alias="from", description="Inclusive lower bound on audit_logs.created_at"),
    ] = None,
    to_ts: Annotated[
        datetime | None,
        Query(alias="to", description="Exclusive upper bound on audit_logs.created_at"),
    ] = None,
    current_user: User = require_role(UserRole.judge, UserRole.admin),
) -> CostSummaryResponse:
    """Return total LLM cost (USD) across audit_logs.

    Authorization:
    - Judges see only their own cases (filtered by `cases.created_by`).
    - Admins see everything.

    Side-effect: updates the per-case Prometheus gauge so /metrics
    reflects the latest rollup.
    """
    cost_col = func.coalesce(func.sum(AuditLog.cost_usd), 0)
    count_col = func.count(AuditLog.id)
    stmt = select(cost_col, count_col)

    if case_id is not None:
        target = (
            await db.execute(select(Case).where(Case.id == case_id))
        ).scalar_one_or_none()
        if target is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found")
        if current_user.role == UserRole.judge and target.created_by != current_user.id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found")
        stmt = stmt.where(AuditLog.case_id == case_id)
    elif current_user.role == UserRole.judge:
        # Judges querying without a case filter see only their own.
        stmt = stmt.join(Case, Case.id == AuditLog.case_id).where(
            Case.created_by == current_user.id
        )

    if from_ts is not None:
        stmt = stmt.where(AuditLog.created_at >= from_ts)
    if to_ts is not None:
        stmt = stmt.where(AuditLog.created_at < to_ts)

    row = (await db.execute(stmt)).one()
    total_raw, audit_count = row
    total = Decimal(total_raw or 0)

    # Update Prometheus gauge for the per-case case-cost rollup.
    if case_id is not None:
        metrics_store.set_case_cost(str(case_id), float(total))

    return CostSummaryResponse(
        case_id=case_id,
        total_usd=total,
        audit_row_count=int(audit_count or 0),
        from_ts=from_ts,
        to_ts=to_ts,
    )
