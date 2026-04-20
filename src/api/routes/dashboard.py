from fastapi import APIRouter
from sqlalchemy import func, select

from src.api.deps import CurrentUser, DBSession
from src.api.schemas.dashboard import DashboardStats
from src.models.case import Case
from src.shared.circuit_breaker import get_pair_search_breaker

router = APIRouter()


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #


@router.get(
    "/stats",
    response_model=DashboardStats,
    operation_id="get_dashboard_stats",
    summary="Get dashboard statistics",
    description="Aggregate case statistics: total count, breakdown by status and domain, "
    "recent cases, and PAIR API circuit breaker health.",
)
async def get_stats(db: DBSession, current_user: CurrentUser) -> dict:
    # Total
    total = (await db.execute(select(func.count(Case.id)))).scalar_one()

    # By status
    status_rows = (
        await db.execute(select(Case.status, func.count(Case.id)).group_by(Case.status))
    ).all()
    by_status = {row[0].value: row[1] for row in status_rows}

    # By domain
    domain_rows = (
        await db.execute(select(Case.domain, func.count(Case.id)).group_by(Case.domain))
    ).all()
    by_domain = {row[0].value: row[1] for row in domain_rows}

    # Recent cases (last 10)
    recent_result = await db.execute(select(Case).order_by(Case.created_at.desc()).limit(10))
    recent = [
        {
            "id": str(c.id),
            "domain": c.domain.value,
            "status": c.status.value,
            "created_at": c.created_at.isoformat() if c.created_at else None,
        }
        for c in recent_result.scalars().all()
    ]

    pair_status = await get_pair_search_breaker().get_status()

    return {
        "total_cases": total,
        "by_status": by_status,
        "by_domain": by_domain,
        "recent_cases": recent,
        "pair_api_status": pair_status,
    }
